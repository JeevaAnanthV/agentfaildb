"""
AutoGen framework runner for AgentFailDB.

Uses GroupChat + GroupChatManager for multi-agent coordination.
Messages are extracted post-hoc from groupchat.messages after
initiate_chat() completes.

Framework imports are deferred to method bodies to avoid ImportError
when AutoGen is not installed.
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

# AutoGen termination token
_TERMINATE = "TERMINATE"


class AutoGenRunner(BaseRunner):
    """Runs a benchmark task using the AutoGen multi-agent framework."""

    framework_name = "autogen"

    def __init__(self, task: BaseTask, collector: TraceCollector | None = None) -> None:
        super().__init__(task, collector)
        self._agents: list[Any] = []
        self._groupchat: Any = None
        self._manager: Any = None
        self._proxy: Any = None

    def setup_agents(self) -> None:
        """Initialise AutoGen agents, GroupChat, and GroupChatManager."""
        import autogen  # noqa: PLC0415

        role_mappings = self.task.framework_role_mappings.get("autogen", {})

        llm_config = {
            "config_list": [
                {
                    "model": settings.task_model,
                    "base_url": settings.ollama_base_url,
                    "api_key": "ollama",
                }
            ],
            "timeout": settings.run_timeout_seconds,
            "max_tokens": settings.max_tokens_per_run,
        }

        self._agents = []
        for canonical_role, role_description in self.task.canonical_roles.items():
            framework_role_name = role_mappings.get(canonical_role, canonical_role)
            system_message = (
                f"You are the {framework_role_name}. {role_description}\n"
                f"Task context: {self.task.description}\n"
                f"When the task is complete, include the word TERMINATE in your final message."
            )
            agent = autogen.AssistantAgent(
                name=framework_role_name,
                system_message=system_message,
                llm_config=llm_config,
            )
            self._agents.append(agent)

        # UserProxy acts as the initiator (does not call LLM)
        self._proxy = autogen.UserProxyAgent(
            name="TaskOrchestrator",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=settings.max_messages_per_run,
            is_termination_msg=lambda x: _TERMINATE in x.get("content", "").upper(),
            code_execution_config=False,
        )

        self._groupchat = autogen.GroupChat(
            agents=[self._proxy] + self._agents,
            messages=[],
            max_round=settings.max_messages_per_run,
            speaker_selection_method="round_robin",
        )

        manager_llm_config = dict(llm_config)
        self._manager = autogen.GroupChatManager(
            groupchat=self._groupchat,
            llm_config=manager_llm_config,
        )

    def run_task(self) -> str:
        """Execute the AutoGen groupchat and extract messages post-hoc."""
        if self._proxy is None or self._manager is None:
            raise RuntimeError("setup_agents() must be called before run_task()")

        initiation_message = (
            f"Please complete the following task collaboratively:\n\n{self.task.description}\n\n"
            f"Work through this task systematically. When fully complete, the last agent "
            f"should include TERMINATE in their response."
        )

        self._proxy.initiate_chat(
            self._manager,
            message=initiation_message,
        )

        # Extract messages from groupchat post-hoc
        final_output = self._extract_messages_and_get_output()
        return final_output

    def _extract_messages_and_get_output(self) -> str:
        """
        Iterate groupchat.messages and record them in the TraceCollector.

        Tags TERMINATE and speaker-selection messages as SYSTEM_CONTROL.
        Returns the final content message as the task output.
        """
        if self._groupchat is None:
            return ""

        messages = getattr(self._groupchat, "messages", [])
        agent_names = [a.name for a in self._agents]
        role_mappings = self.task.framework_role_mappings.get("autogen", {})
        # Reverse mapping: framework_name → canonical_name
        reverse_mapping = {v: k for k, v in role_mappings.items()}

        final_output = ""
        last_content = ""

        for i, msg in enumerate(messages):
            speaker = msg.get("name", msg.get("role", "unknown"))
            content = msg.get("content", "")

            if not content:
                continue

            # Determine message type
            is_termination = _TERMINATE in content.upper()
            is_system = speaker in ("TaskOrchestrator", "GroupChatManager") or is_termination

            if is_system:
                msg_type = MessageType.SYSTEM_CONTROL
            else:
                msg_type = MessageType.RESPONSE

            # Map speaker to canonical role
            canonical_source = reverse_mapping.get(speaker, speaker.lower())

            # Determine target: next agent in round-robin, or orchestrator
            if not is_system and agent_names:
                try:
                    speaker_idx = agent_names.index(speaker)
                    next_idx = (speaker_idx + 1) % len(agent_names)
                    target_name = agent_names[next_idx]
                    canonical_target = reverse_mapping.get(target_name, target_name.lower())
                except ValueError:
                    canonical_target = "orchestrator"
            else:
                canonical_target = "orchestrator"

            self.collector.record(
                source_agent=canonical_source,
                target_agent=canonical_target,
                content=content,
                message_type=msg_type,
                model_used=settings.task_model,
                metadata={"autogen_message_index": i, "speaker": speaker},
            )

            if msg_type == MessageType.RESPONSE:
                last_content = content

        # Final output is the last non-system message
        final_output = last_content.replace(_TERMINATE, "").strip()
        return final_output

    def teardown(self) -> None:
        """Release AutoGen resources."""
        self._agents = []
        self._groupchat = None
        self._manager = None
        self._proxy = None
