"""
MetaGPT framework runner for AgentFailDB.

Monkey-patches Environment.publish_message to intercept all inter-agent
messages before they are routed, recording them via TraceCollector.

Framework imports are deferred to method bodies to avoid ImportError
when MetaGPT is not installed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentfaildb.config import settings
from agentfaildb.harness.trace_collector import TraceCollector
from agentfaildb.runners.base_runner import BaseRunner
from agentfaildb.tasks.base_task import BaseTask
from agentfaildb.trace import MessageType

logger = logging.getLogger(__name__)


class MetaGPTRunner(BaseRunner):
    """Runs a benchmark task using the MetaGPT multi-role framework."""

    framework_name = "metagpt"

    def __init__(self, task: BaseTask, collector: TraceCollector | None = None) -> None:
        super().__init__(task, collector)
        self._team: Any = None
        self._original_publish: Any = None
        self._roles: list[Any] = []

    def setup_agents(self) -> None:
        """Configure MetaGPT, build roles, and install publish_message hook."""
        # All imports deferred to avoid import errors if MetaGPT is not installed
        try:
            from metagpt.config2 import Config  # noqa: PLC0415
            from metagpt.environment import Environment  # noqa: PLC0415
            from metagpt.roles.role import Role  # noqa: PLC0415
            from metagpt.team import Team  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "MetaGPT is not installed. Install it with: pip install metagpt"
            ) from exc

        # Configure MetaGPT to use Ollama
        config = Config.default()
        config.llm.base_url = settings.ollama_base_url
        config.llm.model = settings.task_model
        config.llm.api_key = "ollama"

        role_mappings = self.task.framework_role_mappings.get("metagpt", {})
        collector = self.collector
        task_model = settings.task_model

        # Build minimal Role subclasses for each canonical role
        self._roles = []
        for canonical_role, role_description in self.task.canonical_roles.items():
            framework_role_name = role_mappings.get(canonical_role, canonical_role)

            # Dynamically create a Role subclass
            role_instance = Role(
                name=framework_role_name,
                profile=framework_role_name,
                goal=f"Complete your portion of: {self.task.description}",
                constraints=role_description,
            )
            self._roles.append((canonical_role, role_instance))

        # Install monkey-patch on Environment.publish_message
        original_publish = Environment.publish_message

        def patched_publish_message(env_self: Any, message: Any, peekable: bool = True) -> bool:
            """Intercept MetaGPT message publishing for tracing."""
            try:
                content = ""
                if hasattr(message, "content"):
                    content = str(message.content)
                elif hasattr(message, "instruct_content"):
                    content = str(message.instruct_content)

                if content:
                    # Determine source agent from message role or cause
                    source = "unknown"
                    if hasattr(message, "role"):
                        source = str(message.role)
                    elif hasattr(message, "sent_from"):
                        source = str(message.sent_from)

                    # Reverse-map framework name to canonical role
                    reverse_map = {v: k for k, v in role_mappings.items()}
                    canonical_source = reverse_map.get(source, source.lower())

                    # Target: next role in sequence or orchestrator
                    roles_list = [r[0] for r in self._roles]
                    try:
                        src_idx = roles_list.index(canonical_source)
                        next_idx = src_idx + 1
                        canonical_target = roles_list[next_idx] if next_idx < len(roles_list) else "orchestrator"
                    except ValueError:
                        canonical_target = "orchestrator"

                    collector.record(
                        source_agent=canonical_source,
                        target_agent=canonical_target,
                        content=content,
                        message_type=MessageType.SUBSCRIPTION_ROUTING
                        if not content.strip()
                        else MessageType.RESPONSE,
                        model_used=task_model,
                        metadata={"metagpt_role": source},
                    )
            except Exception as patch_exc:
                logger.warning("MetaGPT publish_message hook error: %s", patch_exc)

            # Call original method
            return original_publish(env_self, message, peekable)

        self._original_publish = original_publish
        Environment.publish_message = patched_publish_message  # type: ignore[method-assign]

        # Build Team
        team = Team()
        for _, role_instance in self._roles:
            team.hire([role_instance])

        # Invest budget to limit token usage
        team.invest(settings.max_tokens_per_run / 1000.0)
        self._team = team

    def run_task(self) -> str:
        """Run MetaGPT team and return the final output."""
        if self._team is None:
            raise RuntimeError("setup_agents() must be called before run_task()")

        # Record initial delegation
        canonical_roles = list(self.task.canonical_roles.keys())
        if canonical_roles:
            self.collector.record(
                source_agent="orchestrator",
                target_agent=canonical_roles[0],
                content=self.task.description,
                message_type=MessageType.TASK_DELEGATION,
                model_used=settings.task_model,
            )

        # MetaGPT uses async — run in event loop
        async def _run() -> str:
            await self._team.run(idea=self.task.description, n_round=len(canonical_roles) + 2)
            # Extract final output from team history
            history = getattr(self._team, "history", None)
            if history:
                messages_hist = getattr(history, "storage", [])
                if messages_hist:
                    last = messages_hist[-1]
                    if hasattr(last, "content"):
                        return str(last.content)
            return ""

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(_run())
            loop.close()
            return result
        except Exception as exc:
            logger.exception("MetaGPT run failed: %s", exc)
            return ""

    def teardown(self) -> None:
        """Restore monkey-patched method and release resources."""
        if self._original_publish is not None:
            try:
                from metagpt.environment import Environment  # noqa: PLC0415
                Environment.publish_message = self._original_publish  # type: ignore[method-assign]
            except ImportError:
                pass
        self._team = None
        self._roles = []
        self._original_publish = None
