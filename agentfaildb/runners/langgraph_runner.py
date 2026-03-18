"""
LangGraph framework runner for AgentFailDB.

Builds a StateGraph with one node per agent role. A custom
BaseCallbackHandler subclass hooks into on_llm_end to record
messages via TraceCollector.

Framework imports are deferred to method bodies to avoid ImportError
when LangGraph/LangChain is not installed.
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


class LangGraphRunner(BaseRunner):
    """Runs a benchmark task using the LangGraph stateful graph framework."""

    framework_name = "langgraph"

    def __init__(self, task: BaseTask, collector: TraceCollector | None = None) -> None:
        super().__init__(task, collector)
        self._graph: Any = None
        self._compiled: Any = None
        self._current_agent: str = ""
        self._agent_sequence: list[str] = []

    def setup_agents(self) -> None:
        """Build the LangGraph StateGraph with one node per agent role."""
        from langchain_core.callbacks.base import BaseCallbackHandler  # noqa: PLC0415
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
        from langgraph.graph import END, StateGraph  # noqa: PLC0415
        from typing_extensions import TypedDict  # noqa: PLC0415

        role_mappings = self.task.framework_role_mappings.get("langgraph", {})
        canonical_roles = list(self.task.canonical_roles.keys())
        self._agent_sequence = canonical_roles

        collector = self.collector
        task_ref = self.task
        task_model = settings.task_model

        # Callback handler to intercept LLM outputs
        class TraceCallbackHandler(BaseCallbackHandler):
            """Minimal callback hook that records LLM completions."""

            def __init__(self, source_agent: str, target_agent: str) -> None:
                super().__init__()
                self.source_agent = source_agent
                self.target_agent = target_agent

            def on_llm_end(self, response: Any, **kwargs: Any) -> None:
                try:
                    if hasattr(response, "generations") and response.generations:
                        gen = response.generations[0]
                        if gen and hasattr(gen[0], "text"):
                            content = gen[0].text
                        elif gen and hasattr(gen[0], "message"):
                            content = str(gen[0].message.content)
                        else:
                            return
                    else:
                        return

                    token_count = None
                    if hasattr(response, "llm_output") and response.llm_output:
                        usage = response.llm_output.get("token_usage", {})
                        token_count = usage.get("completion_tokens")

                    collector.record(
                        source_agent=self.source_agent,
                        target_agent=self.target_agent,
                        content=content,
                        message_type=MessageType.RESPONSE,
                        api_token_count=token_count,
                        model_used=task_model,
                    )
                except Exception as cb_exc:
                    logger.warning("LangGraph callback error: %s", cb_exc)

        # State schema as TypedDict
        class AgentState(TypedDict):
            messages: list[Any]
            current_agent: str
            task_output: str

        # Build LLM instance.
        # Note: num_ctx is an Ollama-native parameter not accepted by the
        # OpenAI-compatible /v1 endpoint via ChatOpenAI's model_kwargs.
        # Ollama's default context window (4096) is sufficient for our tasks.
        llm = ChatOpenAI(
            model=settings.task_model,
            base_url=settings.ollama_base_url,
            api_key="ollama",
            max_tokens=settings.max_tokens_per_run // max(len(canonical_roles), 1),
        )

        # Build node functions for each agent
        node_fns: dict[str, Any] = {}

        for i, canonical_role in enumerate(canonical_roles):
            role_description = task_ref.canonical_roles[canonical_role]
            next_role = canonical_roles[i + 1] if i + 1 < len(canonical_roles) else "END"
            framework_role = role_mappings.get(canonical_role, canonical_role)

            # Create callback handler for this agent
            source = canonical_role
            target = next_role if next_role != "END" else "orchestrator"
            handler = TraceCallbackHandler(source_agent=source, target_agent=target)

            def make_node(
                _role: str,
                _desc: str,
                _handler: TraceCallbackHandler,
                _llm: Any,
                _next: str,
                _fr: str,
            ) -> Any:
                def node_fn(state: AgentState) -> AgentState:
                    system_prompt = (
                        f"You are the {_fr}. {_desc}\nComplete your assigned portion of the task."
                    )
                    # Build messages from state
                    history = state.get("messages", [])
                    msgs = [SystemMessage(content=system_prompt)]
                    if history:
                        msgs.extend(history[-4:])  # keep last 4 context messages
                    else:
                        msgs.append(HumanMessage(content=task_ref.description))

                    try:
                        response = _llm.invoke(
                            msgs,
                            config={"callbacks": [_handler]},
                        )
                        content = response.content
                    except Exception as node_exc:
                        logger.warning("Node %s failed: %s", _role, node_exc)
                        content = f"[{_role} encountered an error: {node_exc}]"

                    new_messages = list(history) + [HumanMessage(content=content, name=_role)]
                    return {
                        "messages": new_messages,
                        "current_agent": _next,
                        "task_output": content
                        if _next in ("END", "orchestrator")
                        else state.get("task_output", ""),
                    }

                return node_fn

            node_fns[canonical_role] = make_node(
                canonical_role, role_description, handler, llm, next_role, framework_role
            )

        # Assemble the graph
        graph = StateGraph(AgentState)
        for canonical_role, fn in node_fns.items():
            graph.add_node(canonical_role, fn)

        # Linear edges
        for i, canonical_role in enumerate(canonical_roles):
            if i + 1 < len(canonical_roles):
                graph.add_edge(canonical_role, canonical_roles[i + 1])
            else:
                graph.add_edge(canonical_role, END)

        graph.set_entry_point(canonical_roles[0])
        self._compiled = graph.compile()

    def run_task(self) -> str:
        """Run the compiled LangGraph and return final output."""
        if self._compiled is None:
            raise RuntimeError("setup_agents() must be called before run_task()")

        # Record initial delegation from orchestrator to first agent
        canonical_roles = list(self.task.canonical_roles.keys())
        if canonical_roles:
            self.collector.record(
                source_agent="orchestrator",
                target_agent=canonical_roles[0],
                content=self.task.description,
                message_type=MessageType.TASK_DELEGATION,
                model_used=settings.task_model,
            )

        initial_state = {
            "messages": [],
            "current_agent": canonical_roles[0] if canonical_roles else "",
            "task_output": "",
        }

        final_state = self._compiled.invoke(initial_state)
        return final_state.get("task_output", "")

    def teardown(self) -> None:
        """Release LangGraph resources."""
        self._graph = None
        self._compiled = None
        self._agent_sequence = []
