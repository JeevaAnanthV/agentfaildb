"""
CrewAI framework runner for AgentFailDB.

Hooks into CrewAI's step_callback to record every agent step as a
normalised AgentMessage via the TraceCollector.

Framework imports are deferred to method bodies to avoid ImportError
when CrewAI is not installed.
"""

from __future__ import annotations

import logging
from typing import Any

from agentfaildb.config import settings
from agentfaildb.harness.trace_collector import TraceCollector
from agentfaildb.runners.base_runner import BaseRunner
from agentfaildb.tasks.base_task import BaseTask
from agentfaildb.trace import MessageType

logger = logging.getLogger(__name__)


class CrewAIRunner(BaseRunner):
    """Runs a benchmark task using the CrewAI multi-agent framework."""

    framework_name = "crewai"

    def __init__(self, task: BaseTask, collector: TraceCollector | None = None) -> None:
        super().__init__(task, collector)
        self._crew: Any = None
        self._agents: dict[str, Any] = {}
        self._tasks: list[Any] = []
        self._step_count: int = 0

    def setup_agents(self) -> None:
        """Initialise CrewAI agents, tasks, and crew with step_callback hook."""
        from crewai import LLM, Agent, Crew, Task  # noqa: PLC0415

        role_mappings = self.task.framework_role_mappings.get("crewai", {})

        llm = LLM(
            model=f"openai/{settings.task_model}",
            base_url=settings.ollama_base_url,
            api_key="ollama",
            extra_headers={},
            # Pass Ollama context window size
            model_kwargs={"num_ctx": settings.ollama_num_ctx},
        )

        # Build agents from canonical roles
        self._agents = {}
        for canonical_role, role_description in self.task.canonical_roles.items():
            framework_role_name = role_mappings.get(canonical_role, canonical_role)
            agent = Agent(
                role=framework_role_name,
                goal=f"Complete your assigned portion of the task as {framework_role_name}.",
                backstory=role_description,
                llm=llm,
                verbose=False,
                allow_delegation=False,
            )
            self._agents[canonical_role] = agent

        # Build sequential tasks — each agent gets a portion of the work
        canonical_roles_list = list(self.task.canonical_roles.keys())
        self._tasks = []
        for i, canonical_role in enumerate(canonical_roles_list):
            agent = self._agents[canonical_role]
            if i == 0:
                task_description = self.task.description
            else:
                task_description = (
                    f"Review the previous agent's work and contribute your perspective as "
                    f"{canonical_role}. Original task: {self.task.description}"
                )
            crew_task = Task(
                description=task_description,
                agent=agent,
                expected_output=self.task.expected_output_description or "A complete response.",
            )
            self._tasks.append(crew_task)

        # step_callback captures each agent step
        collector = self.collector
        step_count_ref = [0]
        agents_list = canonical_roles_list

        def step_callback(step_output: Any) -> None:
            """Record each agent step in the TraceCollector."""
            try:
                idx = step_count_ref[0]
                step_count_ref[0] += 1

                # Determine source/target from step index
                agent_idx = idx % len(agents_list)
                next_idx = (agent_idx + 1) % len(agents_list)
                source = agents_list[agent_idx]
                target = agents_list[next_idx] if idx < len(agents_list) - 1 else "orchestrator"

                content = ""
                msg_type = MessageType.RESPONSE

                if hasattr(step_output, "thought"):
                    content = str(step_output.thought or "")
                    msg_type = MessageType.INTERNAL_REASONING
                elif hasattr(step_output, "output"):
                    content = str(step_output.output or "")
                    msg_type = MessageType.RESPONSE
                elif hasattr(step_output, "result"):
                    content = str(step_output.result or "")
                    msg_type = MessageType.RESPONSE
                else:
                    content = str(step_output)

                if not content:
                    return

                collector.record(
                    source_agent=source,
                    target_agent=target,
                    content=content,
                    message_type=msg_type,
                    model_used=settings.task_model,
                    metadata={"step_index": idx},
                )
            except Exception as cb_exc:
                logger.warning("step_callback error: %s", cb_exc)

        self._crew = Crew(
            agents=list(self._agents.values()),
            tasks=self._tasks,
            verbose=False,
            step_callback=step_callback,
        )

    def run_task(self) -> str:
        """Execute the CrewAI crew and return the final output."""
        if self._crew is None:
            raise RuntimeError("setup_agents() must be called before run_task()")

        result = self._crew.kickoff()

        # Record final output as a RESPONSE from last agent to orchestrator
        final_output = ""
        if hasattr(result, "raw"):
            final_output = str(result.raw)
        elif hasattr(result, "output"):
            final_output = str(result.output)
        else:
            final_output = str(result)

        agents_list = list(self.task.canonical_roles.keys())
        if agents_list:
            self.collector.record(
                source_agent=agents_list[-1],
                target_agent="orchestrator",
                content=final_output,
                message_type=MessageType.RESPONSE,
                model_used=settings.task_model,
                metadata={"is_final_output": True},
            )

        return final_output

    def teardown(self) -> None:
        """Release CrewAI resources."""
        self._crew = None
        self._agents = {}
        self._tasks = []
