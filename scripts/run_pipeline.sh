#!/usr/bin/env bash
# Runs ON the 3050 box. Installs framework deps, smoke-tests one task, and —
# only if the smoke test actually stores a trace — launches the full benchmark
# runner (checkpoint/resume, detached).
#
# Launched as:  setsid nohup bash scripts/run_pipeline.sh > run_pipeline.log 2>&1 &
set -x
cd "$HOME/agentfaildb" || exit 1
PROJ="$HOME/agentfaildb"
PY="$PROJ/venv/bin/python"
PIP="$PROJ/venv/bin/pip"

# Load .env so POSTGRES_PORT=5435 / OLLAMA_BASE_URL etc. are visible to Python
set -a; [ -f .env ] && . ./.env; set +a

# Remove the deleted MetaGPT runner if rsync left it behind (no --delete)
rm -f "$PROJ/agentfaildb/runners/metagpt_runner.py" 2>/dev/null

echo "######## STEP 1: install framework deps (crewai, autogen, langgraph) ########"
"$PIP" install -e ".[frameworks]" --retries 12 --timeout 60 2>&1 | tail -20
echo "--- import check ---"
"$PY" - <<'PYEOF' 2>&1
for m in ("crewai", "autogen", "langgraph", "langchain_openai"):
    try:
        __import__(m); print(f"OK   {m}")
    except Exception as e:
        print(f"FAIL {m}: {e}")
PYEOF

echo "######## STEP 2: smoke test (langgraph, 1 task) ########"
"$PY" smoke_test.py --framework langgraph 2>&1 | tail -45
SMOKE_RC=${PIPESTATUS[0]}
echo "SMOKE_RC=$SMOKE_RC"
TRACES=$(docker compose exec -T postgres psql -U agentfaildb -d agentfaildb -tc \
  "SELECT COUNT(*) FROM traces;" 2>/dev/null | tr -d ' ')
echo "TRACES_AFTER_SMOKE=${TRACES:-unknown}"

echo "######## STEP 3: start full benchmark (gated on smoke) ########"
if [ "$SMOKE_RC" = "0" ] && [ "${TRACES:-0}" -ge 1 ]; then
  echo "SMOKE_PASSED — launching full benchmark runner (detached, checkpoint/resume)"
  setsid nohup ./agentfaildb-runner.sh > runner_console.log 2>&1 < /dev/null &
  echo "RUNNER_LAUNCHED pid=$!"
else
  echo "SMOKE_FAILED — NOT starting the full run. Inspect the smoke output above."
fi
echo "######## PIPELINE DONE ########"
