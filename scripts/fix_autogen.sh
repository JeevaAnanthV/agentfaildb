#!/usr/bin/env bash
# Runs ON the 3050 box. Builds an ISOLATED venv for AutoGen (so it can't perturb
# the dependencies of the live crewai+langgraph run), installs the classic
# autogen API (ag2, with a pyautogen==0.2.35 fallback), smoke-tests one autogen
# task, and — only if that lands a trace — installs+starts a reboot-proof
# systemd service that runs the autogen benchmark (checkpoint/resume).
#
# Launch: SUDOPASS=... setsid nohup bash scripts/fix_autogen.sh > fix_autogen.log 2>&1 &
set -x
cd "$HOME/agentfaildb" || exit 1
PROJ="$HOME/agentfaildb"
VA="$PROJ/venv-autogen"
SUDO() { echo "$SUDOPASS" | sudo -S "$@"; }
set -a; [ -f .env ] && . ./.env; set +a

echo "######## A1: build isolated venv-autogen ########"
python3 -m venv "$VA"
"$VA/bin/python" -m pip install --upgrade pip wheel setuptools --retries 12 --timeout 60 2>&1 | tail -2
"$VA/bin/pip" install -e . --retries 12 --timeout 60 2>&1 | tail -3

echo "######## A2: install autogen (classic API via ag2) ########"
"$VA/bin/pip" uninstall -y autogen-core autogen-agentchat pyautogen autogen ag2 2>/dev/null
"$VA/bin/pip" install "ag2[openai]" --retries 12 --timeout 60 2>&1 | tail -4
if ! "$VA/bin/python" -c "from autogen import AssistantAgent, UserProxyAgent, GroupChat, GroupChatManager" 2>/dev/null; then
  echo "ag2 classic API import failed — falling back to pyautogen==0.2.35"
  "$VA/bin/pip" uninstall -y ag2 autogen 2>/dev/null
  "$VA/bin/pip" install "pyautogen==0.2.35" --retries 12 --timeout 60 2>&1 | tail -4
fi
echo "--- import check ---"
"$VA/bin/python" -c "import autogen; from autogen import AssistantAgent, UserProxyAgent, GroupChat, GroupChatManager; print('AUTOGEN_API_OK', getattr(autogen,'__version__','?'))" 2>&1

echo "######## A3: smoke autogen ########"
"$VA/bin/python" smoke_test.py --framework autogen 2>&1 | tail -45
SMOKE_RC=${PIPESTATUS[0]}
echo "SMOKE_AUTOGEN_RC=$SMOKE_RC"
AG=$(docker compose exec -T postgres psql -U agentfaildb -d agentfaildb -tc \
  "SELECT COUNT(*) FROM traces WHERE framework='autogen';" 2>/dev/null | tr -d ' ')
echo "AUTOGEN_TRACES=${AG:-unknown}"

echo "######## A4: reboot-proof systemd autogen service (gated on smoke) ########"
if [ "$SMOKE_RC" = "0" ] && [ "${AG:-0}" -ge 1 ]; then
  cat > /tmp/afdb-autogen.service <<UNIT
[Unit]
Description=AgentFailDB benchmark (autogen)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=visiongaurd
WorkingDirectory=$PROJ
ExecStart=$VA/bin/python -m agentfaildb.harness.orchestrator --frameworks autogen
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT
  SUDO cp /tmp/afdb-autogen.service /etc/systemd/system/agentfaildb-bench-autogen.service
  SUDO systemctl daemon-reload
  SUDO systemctl enable --now agentfaildb-bench-autogen 2>&1
  sleep 6
  echo "autogen service: $(SUDO systemctl is-active agentfaildb-bench-autogen 2>/dev/null) / $(SUDO systemctl is-enabled agentfaildb-bench-autogen 2>/dev/null)"
  echo "AUTOGEN_SERVICE_STARTED"
else
  echo "AUTOGEN SMOKE FAILED — service not created. Inspect the smoke output above."
fi
echo "######## FIX_AUTOGEN DONE ########"
