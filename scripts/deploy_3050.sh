#!/usr/bin/env bash
# Detached deploy script — runs ON the 3050 box (visiongaurd).
# Brings up the AgentFailDB docker stack with restart:always and installs
# the dashboard as a systemd service (Restart=always) on port 1221.
#
# Launched as:  SUDOPASS=... nohup bash scripts/deploy_3050.sh > deploy_3050.log 2>&1 &
set -x
cd "$HOME/agentfaildb" || exit 1
PROJ="$HOME/agentfaildb"
SUDO() { echo "$SUDOPASS" | sudo -S "$@"; }

echo "######## STEP 1: NVIDIA docker runtime ########"
SUDO nvidia-ctk runtime configure --runtime=docker 2>&1
SUDO systemctl restart docker 2>&1
sleep 5
docker info 2>/dev/null | grep -i "runtime" || true

echo "######## STEP 2: restart:always override ########"
cat > "$PROJ/docker-compose.override.yml" <<'YAML'
services:
  postgres:
    restart: always
  redis:
    restart: always
  ollama:
    restart: always
YAML
cat "$PROJ/docker-compose.override.yml"

echo "######## STEP 3: bring up infra (postgres, redis, ollama, ollama-init) ########"
# Pulls images on the box's own network; model pull runs async in ollama-init.
docker compose up -d postgres redis ollama ollama-init 2>&1
echo "--- compose ps ---"
docker compose ps 2>&1

echo "######## STEP 4: dashboard venv ########"
if [ ! -d "$PROJ/venv" ]; then
  python3 -m venv "$PROJ/venv"
fi
"$PROJ/venv/bin/pip" install --upgrade pip wheel setuptools 2>&1 | tail -3
"$PROJ/venv/bin/pip" install -e "$PROJ" jinja2 2>&1 | tail -5
"$PROJ/venv/bin/python" -c "import fastapi, uvicorn, psutil, psycopg2, jinja2; print('dashboard deps OK')" 2>&1

echo "######## STEP 5: systemd dashboard service (Restart=always) ########"
SUDO tee /etc/systemd/system/agentfaildb-dashboard.service > /dev/null <<UNIT
[Unit]
Description=AgentFailDB Dashboard (port 1221)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=visiongaurd
WorkingDirectory=$PROJ
EnvironmentFile=$PROJ/.env
ExecStart=$PROJ/venv/bin/python -m agentfaildb.dashboard
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
SUDO systemctl daemon-reload 2>&1
SUDO systemctl enable --now agentfaildb-dashboard 2>&1
sleep 4
SUDO systemctl status agentfaildb-dashboard --no-pager 2>&1 | head -15

echo "######## STEP 6: verification ########"
echo "--- nvidia-smi ---"; nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv 2>&1 | head
echo "--- ollama GPU detection (container log) ---"; docker compose logs ollama 2>&1 | grep -i "gpu\|cuda\|vram\|library" | tail -8
echo "--- ollama-init (model pull) ---"; docker compose logs ollama-init 2>&1 | tail -8
echo "--- ollama tags ---"; curl -s http://localhost:11435/api/tags 2>&1 | head -c 500; echo
echo "--- dashboard http ---"; curl -s -o /dev/null -w "dashboard HTTP %{http_code}\n" http://localhost:1221/ 2>&1
echo "--- final compose ps ---"; docker compose ps 2>&1
echo "######## DEPLOY SCRIPT DONE ########"
