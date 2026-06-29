#!/usr/bin/env bash
# v2 deploy — fixes the three v1 failures:
#  (1) compose up aborted on flaky-WiFi image-pull timeout -> retry pull in bg
#  (2) python3 -m venv failed (missing python3-venv apt pkg) -> apt install it
#  (3) systemd unit masked (sudo -S stdin clashed with heredoc) -> temp-file install
# Brings DB + Redis + dashboard up FAST (no waiting on the 4GB Ollama image).
set -x
cd "$HOME/agentfaildb" || exit 1
PROJ="$HOME/agentfaildb"
SUDO() { echo "$SUDOPASS" | sudo -S "$@"; }

echo "######## FIX 0: clear masked/broken dashboard unit ########"
SUDO systemctl unmask agentfaildb-dashboard 2>&1
SUDO rm -f /etc/systemd/system/agentfaildb-dashboard.service 2>&1
SUDO systemctl daemon-reload 2>&1

echo "######## FIX 1: install python3-venv ########"
SUDO apt-get update -y 2>&1 | tail -2
SUDO apt-get install -y python3-venv python3.12-venv 2>&1 | tail -4

echo "######## FIX 2: start postgres + redis (images already pulled) ########"
docker compose up -d postgres redis 2>&1
docker compose ps --format "{{.Service}}: {{.Status}}" 2>&1

echo "######## FIX 3: dashboard venv + deps ########"
rm -rf "$PROJ/venv"
python3 -m venv "$PROJ/venv" 2>&1
echo "venv python: $(ls -la "$PROJ/venv/bin/python"* 2>&1)"
"$PROJ/venv/bin/python" -m pip install --upgrade pip wheel setuptools --retries 12 --timeout 60 2>&1 | tail -2
"$PROJ/venv/bin/python" -m pip install -e "$PROJ" jinja2 --retries 12 --timeout 60 2>&1 | tail -4
"$PROJ/venv/bin/python" -c "import fastapi, uvicorn, psutil, psycopg2, jinja2; print('DASHBOARD_DEPS_OK')" 2>&1

echo "######## FIX 4: systemd dashboard unit (temp-file install, no stdin clash) ########"
cat > /tmp/afdb-dash.service <<UNIT
[Unit]
Description=AgentFailDB Dashboard (port 1221)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=visiongaurd
WorkingDirectory=$PROJ
ExecStart=$PROJ/venv/bin/python -m agentfaildb.dashboard
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
SUDO cp /tmp/afdb-dash.service /etc/systemd/system/agentfaildb-dashboard.service
SUDO systemctl daemon-reload 2>&1
SUDO systemctl enable --now agentfaildb-dashboard 2>&1
sleep 5
echo "is-active: $(SUDO systemctl is-active agentfaildb-dashboard 2>&1)"
echo "is-enabled: $(SUDO systemctl is-enabled agentfaildb-dashboard 2>&1)"
SUDO systemctl status agentfaildb-dashboard --no-pager 2>&1 | head -18
echo "--- dashboard logs (last 15) ---"
SUDO journalctl -u agentfaildb-dashboard --no-pager -n 15 2>&1

echo "######## VERIFY CORE ########"
curl -s -o /dev/null -w "dashboard HTTP %{http_code}\n" --max-time 10 http://localhost:1221/ 2>&1
docker compose ps --format "{{.Service}}: {{.Status}}" 2>&1

echo "######## FIX 5: launch background Ollama pull-retry loop ########"
cat > /tmp/ollama_pull.sh <<'OPULL'
cd "$HOME/agentfaildb" || exit 1
for i in $(seq 1 60); do
  echo "=== [ollama pull attempt $i $(date +%H:%M:%S)] ==="
  docker compose pull ollama 2>&1 | tail -2
  if docker compose up -d ollama ollama-init 2>&1; then
    if docker images --format '{{.Repository}}' | grep -q ollama; then
      echo "OLLAMA_IMAGE_PRESENT_AND_STARTED"; break
    fi
  fi
  sleep 15
done
echo "=== ollama pull loop finished ==="
docker compose ps --format "{{.Service}}: {{.Status}}" 2>&1
OPULL
setsid nohup bash /tmp/ollama_pull.sh > "$PROJ/ollama_pull.log" 2>&1 < /dev/null &
echo "ollama pull loop launched pid=$!"
echo "######## CORE DONE ########"
