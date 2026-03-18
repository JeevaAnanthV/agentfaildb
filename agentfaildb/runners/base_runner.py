"""
Abstract base runner for AgentFailDB framework runners.

Every framework-specific runner inherits BaseRunner and implements:
  setup_agents() — initialise agents and framework objects
  run_task()     — execute the task and return final output string
  teardown()     — clean up resources

The non-overridable execute() method orchestrates the full lifecycle,
handles the 120-second timeout, estimates token counts via tiktoken,
and assembles the TaskTrace.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID, uuid4

import tiktoken

from agentfaildb.config import settings
from agentfaildb.harness.trace_collector import TraceCollector
from agentfaildb.tasks.base_task import BaseTask
from agentfaildb.trace import TaskTrace

logger = logging.getLogger(__name__)

_TIKTOKEN_ENCODING = "cl100k_base"


class BaseRunner(ABC):
    """
    Abstract base class for all framework runners.

    Subclasses must implement setup_agents(), run_task(), and teardown().
    Do not override execute().
    """

    framework_name: str = "unknown"

    def __init__(self, task: BaseTask, collector: TraceCollector | None = None) -> None:
        self.task = task
        self.collector: TraceCollector = collector or TraceCollector.get_instance()
        self._trace_id: UUID = uuid4()
        self._encoding: tiktoken.Encoding = tiktoken.get_encoding(_TIKTOKEN_ENCODING)

    @abstractmethod
    def setup_agents(self) -> None:
        """Initialise all framework agents and any supporting objects."""

    @abstractmethod
    def run_task(self) -> str:
        """
        Execute the task using the configured agents.

        Returns the final output string produced by the agent system.
        """

    @abstractmethod
    def teardown(self) -> None:
        """Release any resources acquired in setup_agents()."""

    # ── Non-overridable orchestration ─────────────────────────────────────────

    def execute(self) -> TaskTrace:
        """
        Full lifecycle: setup → run → teardown → build TaskTrace.

        Handles configurable timeout (default 600s), token estimation, and exception capture.
        Never raises — exceptions are stored in run_config["error"].
        """
        import concurrent.futures

        self.collector.start_run(self._trace_id)
        start_time = time.monotonic()
        actual_output = ""
        run_metadata: dict[str, Any] = {}

        try:
            self.setup_agents()
        except Exception as setup_exc:
            logger.exception("setup_agents() failed for task %s", self.task.task_id)
            run_metadata["setup_error"] = str(setup_exc)
            elapsed = time.monotonic() - start_time
            messages = self.collector.flush()
            return self._build_trace(actual_output, elapsed, messages, run_metadata)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.run_task)
                try:
                    actual_output = future.result(timeout=settings.run_timeout_seconds)
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        "Task %s timed out after %ss — waiting up to 30s for partial result",
                        self.task.task_id,
                        settings.run_timeout_seconds,
                    )
                    run_metadata["timeout"] = True
                    # Python threads cannot be forcibly killed; try to collect
                    # whatever the thread has already produced (30s grace period).
                    try:
                        actual_output = future.result(timeout=30)
                        run_metadata["timeout_recovered"] = True
                        logger.info(
                            "Task %s: recovered output after timeout grace period",
                            self.task.task_id,
                        )
                    except (concurrent.futures.TimeoutError, Exception):
                        actual_output = ""
                    future.cancel()
                    # If still empty, extract the last meaningful message content
                    if not actual_output:
                        partial = self.collector.get_messages()
                        if partial:
                            last_msg = partial[-1]
                            actual_output = f"[TIMEOUT — partial output]\n{last_msg.content}"
                            run_metadata["partial_output"] = True
                            logger.info(
                                "Task %s: using partial output from last message (%s→%s)",
                                self.task.task_id,
                                last_msg.source_agent,
                                last_msg.target_agent,
                            )
                except Exception as run_exc:
                    logger.exception("run_task() raised for task %s", self.task.task_id)
                    run_metadata["run_error"] = str(run_exc)
                    actual_output = ""
        finally:
            try:
                self.teardown()
            except Exception as teardown_exc:
                logger.warning("teardown() failed: %s", teardown_exc)
                run_metadata["teardown_error"] = str(teardown_exc)

        elapsed = time.monotonic() - start_time
        messages = self.collector.flush()

        # Fill in missing content_token_count estimates via tiktoken
        for msg in messages:
            if msg.content_token_count is None:
                try:
                    count = len(self._encoding.encode(msg.content))
                    object.__setattr__(msg, "content_token_count", count)
                except Exception:
                    pass

        return self._build_trace(actual_output, elapsed, messages, run_metadata)

    # ── Trace assembly ────────────────────────────────────────────────────────

    def _build_trace(
        self,
        actual_output: str,
        elapsed: float,
        messages: list,
        run_metadata: dict[str, Any],
    ) -> TaskTrace:
        """Assemble a TaskTrace from the collected run data."""
        return TaskTrace(
            trace_id=self._trace_id,
            framework=self.framework_name,
            task_category=self.task.task_category,
            task_difficulty=self.task.difficulty,
            task_id=self.task.task_id,
            task_description=self.task.description,
            ground_truth_type=self.task.ground_truth_type,
            ground_truth=self.task.ground_truth,
            actual_output=actual_output or "",
            messages=messages,
            total_time_seconds=elapsed,
            num_agents=len(self.task.canonical_roles),
            agent_roles=self.task.canonical_roles,
            model_used=settings.task_model,
            run_config={
                "task_id": self.task.task_id,
                "framework": self.framework_name,
                "timeout_seconds": settings.run_timeout_seconds,
                **run_metadata,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for a string using cl100k_base."""
        try:
            return len(self._encoding.encode(text))
        except Exception:
            return len(text) // 4  # rough fallback
