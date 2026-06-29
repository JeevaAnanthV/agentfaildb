#!/usr/bin/env bash
# Non-destructive: install LiteLLM into the main venv (CrewAI 1.x needs it) and
# verify crewai constructs while langgraph still imports. No deletes, no restarts.
set -x
cd "$HOME/agentfaildb" || exit 1
set -a; [ -f .env ] && . ./.env; set +a

echo "######## install litellm ########"
venv/bin/pip install litellm --retries 12 --timeout 60 2>&1 | tail -6

echo "######## verify crewai constructs + langgraph still imports ########"
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
echo "RC=${PIPESTATUS[0]}"
echo "######## INSTALL_LITELLM DONE ########"
