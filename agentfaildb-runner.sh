#!/usr/bin/env bash
# agentfaildb-runner.sh
# Resilient 24/7 benchmark runner for AgentFailDB.
# Survives laptop sleep, shutdown, crashes, and restarts.
# Never re-runs completed tasks (double-checked via PostgreSQL + Redis).
#
# Usage:
#   ./agentfaildb-runner.sh           # run the benchmark (foreground)
#   ./agentfaildb-runner.sh --status  # print progress stats and exit

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
VENV="$PROJECT_DIR/venv"
LOCK_FILE="$PROJECT_DIR/.runner.lock"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/benchmark_$(date +%Y%m%d).log"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

OLLAMA_URL="http://localhost:11435"
OLLAMA_API="$OLLAMA_URL/api/tags"
OLLAMA_MODEL="${TASK_MODEL:-llama3.1:8b}"

DOCKER_WAIT_RETRIES=30   # x 5s = 150s max
OLLAMA_WAIT_RETRIES=60   # x 5s = 300s max (model load can take a while on GTX 1650)

# ── Helpers ───────────────────────────────────────────────────────────────────
log() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $*" | tee -a "$LOG_FILE"
}

die() {
    log "FATAL: $*"
    exit 1
}

# ── --status mode: no lock, no Docker check, just query DB and exit ───────────
if [[ "${1:-}" == "--status" ]]; then
    log "Status check requested."
    if [[ ! -d "$VENV" ]]; then
        die "venv not found at $VENV — run: python -m venv venv && pip install -e '.[dev]'"
    fi
    # shellcheck source=/dev/null
    source "$VENV/bin/activate"
    cd "$PROJECT_DIR"
    # Load .env so Python picks up POSTGRES_PORT etc.
    if [[ -f "$PROJECT_DIR/.env" ]]; then
        set -o allexport
        # shellcheck source=/dev/null
        source "$PROJECT_DIR/.env"
        set +o allexport
    fi
    python -m agentfaildb.harness.orchestrator --status
    exit 0
fi

# ── Lock file: prevent duplicate runners ─────────────────────────────────────
cleanup() {
    log "Cleaning up lock file and exiting."
    rm -f "$LOCK_FILE"
}

sigterm_handler() {
    log "Received SIGTERM — shutting down gracefully."
    cleanup
    exit 0
}

sigint_handler() {
    log "Received SIGINT — shutting down gracefully."
    cleanup
    exit 0
}

trap cleanup EXIT
trap sigterm_handler SIGTERM
trap sigint_handler SIGINT

if [[ -f "$LOCK_FILE" ]]; then
    existing_pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "unknown")
    if kill -0 "$existing_pid" 2>/dev/null; then
        log "Another runner is already active (PID $existing_pid). Exiting."
        # Remove trap so we don't delete the other runner's lock
        trap - EXIT
        exit 0
    else
        log "Stale lock file found (PID $existing_pid no longer running). Removing."
        rm -f "$LOCK_FILE"
    fi
fi

echo "$$" > "$LOCK_FILE"
log "Lock acquired (PID $$). Logging to $LOG_FILE"

# ── Ensure directories ────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── Ensure venv exists ────────────────────────────────────────────────────────
if [[ ! -d "$VENV" ]]; then
    die "venv not found at $VENV. Create it with: python -m venv venv && pip install -e '.[dev]'"
fi

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -o allexport
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.env"
    set +o allexport
    log ".env loaded."
fi

# ── Ensure Docker services are running ───────────────────────────────────────
log "Checking Docker services..."
cd "$PROJECT_DIR"

if ! docker compose -f "$COMPOSE_FILE" ps --services --filter status=running 2>/dev/null | grep -q "postgres"; then
    log "Docker services not running — starting them now."
    docker compose -f "$COMPOSE_FILE" up -d 2>&1 | tee -a "$LOG_FILE"
    log "Docker compose up issued. Waiting for services to become healthy..."
fi

# Wait for postgres to be healthy
postgres_ready=false
for i in $(seq 1 $DOCKER_WAIT_RETRIES); do
    if docker compose -f "$COMPOSE_FILE" exec -T postgres \
        pg_isready -U agentfaildb -d agentfaildb -q 2>/dev/null; then
        postgres_ready=true
        log "PostgreSQL is ready."
        break
    fi
    log "Waiting for PostgreSQL... ($i/$DOCKER_WAIT_RETRIES)"
    sleep 5
done
if [[ "$postgres_ready" != "true" ]]; then
    die "PostgreSQL did not become ready after $((DOCKER_WAIT_RETRIES * 5))s."
fi

# Wait for redis to be healthy
redis_ready=false
for i in $(seq 1 $DOCKER_WAIT_RETRIES); do
    if docker compose -f "$COMPOSE_FILE" exec -T redis \
        redis-cli ping 2>/dev/null | grep -q "PONG"; then
        redis_ready=true
        log "Redis is ready."
        break
    fi
    log "Waiting for Redis... ($i/$DOCKER_WAIT_RETRIES)"
    sleep 5
done
if [[ "$redis_ready" != "true" ]]; then
    log "WARNING: Redis not ready — checkpoint will be DB-only. Continuing."
fi

# ── Wait for Ollama to be healthy and model loaded ────────────────────────────
log "Waiting for Ollama at $OLLAMA_URL ..."
ollama_ready=false
for i in $(seq 1 $OLLAMA_WAIT_RETRIES); do
    if curl -sf "$OLLAMA_API" >/dev/null 2>&1; then
        ollama_ready=true
        break
    fi
    log "Waiting for Ollama... ($i/$OLLAMA_WAIT_RETRIES)"
    sleep 5
done
if [[ "$ollama_ready" != "true" ]]; then
    die "Ollama did not become reachable after $((OLLAMA_WAIT_RETRIES * 5))s."
fi
log "Ollama is reachable."

# Check model is loaded (present in tag list)
log "Checking model '$OLLAMA_MODEL' is available in Ollama..."
model_ready=false
for i in $(seq 1 $OLLAMA_WAIT_RETRIES); do
    # Normalise the model name: strip the ':latest' suffix for grep since
    # Ollama sometimes lists it as 'llama3.1:8b' or 'llama3.1:8b-instruct-q4'.
    model_base="${OLLAMA_MODEL%%:*}"
    if curl -sf "$OLLAMA_API" 2>/dev/null | grep -q "$model_base"; then
        model_ready=true
        log "Model '$OLLAMA_MODEL' is ready."
        break
    fi
    log "Model not yet loaded... ($i/$OLLAMA_WAIT_RETRIES)"
    sleep 5
done
if [[ "$model_ready" != "true" ]]; then
    log "WARNING: Model '$OLLAMA_MODEL' may not be loaded. Continuing anyway (ollama-init may still be pulling)."
fi

# ── Activate venv and run the orchestrator ────────────────────────────────────
log "Activating venv at $VENV"
# shellcheck source=/dev/null
source "$VENV/bin/activate"

log "Starting orchestrator — all tasks x all frameworks, checkpoint/resume enabled."
log "Estimated ~10 min/task on GTX 1650. Check progress: ./agentfaildb-runner.sh --status"

cd "$PROJECT_DIR"
python -m agentfaildb.harness.orchestrator 2>&1 | tee -a "$LOG_FILE"

log "Orchestrator exited."
