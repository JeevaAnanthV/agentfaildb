#!/usr/bin/env bash
# Runs ON the 3050 box. CrewAI 1.x made LiteLLM optional; the runner needs it.
# Install litellm into the main venv, verify crewai constructs (and langgraph still
# imports), then — only if that works — stop the manual run, delete the junk crewai
# traces, and restart crewai+langgraph under systemd (rebuilds checkpoint from the
# cleaned DB; crash+reboot-proof).
#
# Launch: SUDOPASS=... setsid nohup bash scripts/fix_crewai.sh > fix_crewai.log 2>&1 &
set -x
cd "$HOME/agentfaildb" || exit 1
SUDO() { echo "$SUDOPASS" | sudo -S "$@"; }
set -a; [ -f .env ] && . ./.env; set +a
PSQL="docker compose exec -T postgres psql -U agentfaildb -d agentfaildb"

echo "######## C1: install litellm into main venv ########"
venv/bin/pip install litellm --retries 12 --timeout 60 2>&1 | tail -5

echo "######## C2: verify crewai constructs AND langgraph still imports ########"
venv/bin/python - <<'PYEOF'
import langgraph, langchain_openai  # litellm install must not break the live langgraph run
from crewai import LLM, Agent, Crew, Task
llm = LLM(model='openai/llama3.1:8b', base_url='http://localhost:11435/v1',
          api_key='ollama', model_kwargs={'num_ctx': 4096})
a = Agent(role='Researcher', goal='g', backstory='b', llm=llm, verbose=False, allow_delegation=False)
Crew(agents=[a], tasks=[Task(description='hi', agent=a, expected_output='x')],
     verbose=False, step_callback=lambda x: None)
print('CONSTRUCT_OK')
PYEOF
if [ "${PIPESTATUS[0]:-1}" != "0" ]; then
  echo "CONSTRUCT_FAILED — litellm did not fix crewai; live run left untouched, no data deleted."
  echo "######## FIX_CREWAI DONE ########"; exit 1
fi

echo "######## C3: stop the manual crewai+langgraph run ########"
pkill -f "harness.orchestrator --frameworks crewai langgraph" 2>/dev/null
sleep 4
pgrep -af "harness.orchestrator --frameworks crewai langgraph" | grep -v ssh && echo "WARN: still alive" || echo "manual run stopped"

echo "######## C4: delete junk crewai traces (+ children) ########"
$PSQL -c "DELETE FROM annotations WHERE trace_id IN (SELECT trace_id FROM traces WHERE framework='crewai');" 2>&1
$PSQL -c "DELETE FROM messages    WHERE trace_id IN (SELECT trace_id FROM traces WHERE framework='crewai');" 2>&1
$PSQL -c "DELETE FROM traces WHERE framework='crewai';" 2>&1
echo "--- remaining traces by framework ---"
$PSQL -tc "SELECT framework, COUNT(*) FROM traces GROUP BY framework ORDER BY 1;" 2>&1
# clear any stale redis checkpoint so the rebuild is DB-authoritative
docker compose exec -T redis redis-cli DEL agentfaildb:checkpoint 2>&1

echo "######## C5: start crewai+langgraph under systemd (reboot+crash proof) ########"
SUDO systemctl start agentfaildb-bench-main 2>&1
sleep 10
echo "main service: $(SUDO systemctl is-active agentfaildb-bench-main 2>/dev/null)/$(SUDO systemctl is-enabled agentfaildb-bench-main 2>/dev/null)"
SUDO systemctl status agentfaildb-bench-main --no-pager 2>&1 | head -12
echo "--- crewai traces appearing? (wait ~90s for first real one) ---"
sleep 90
$PSQL -tc "SELECT framework, COUNT(*), COUNT(*) FILTER (WHERE task_success) ok, ROUND(AVG(total_content_tokens)::numeric) avgtok FROM traces WHERE framework='crewai' GROUP BY framework;" 2>&1
echo "######## FIX_CREWAI DONE ########"
