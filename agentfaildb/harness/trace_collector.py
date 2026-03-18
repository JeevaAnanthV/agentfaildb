"""
In-process singleton trace collector.

Zero network I/O during recording — messages accumulate in memory and are
batch-flushed to PostgreSQL on run completion.  Thread-safe via a module-level
lock that guards all mutable state.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any
from uuid import UUID

from agentfaildb.trace import AgentMessage, MessageType


class TraceCollector:
    """
    In-process singleton that records inter-agent messages during a framework run.

    Usage pattern::

        collector = get_collector()
        collector.start_run(trace_id)

        # inside framework hooks:
        collector.record(
            source_agent="researcher",
            target_agent="writer",
            content="Here is what I found ...",
            message_type=MessageType.RESPONSE,
            api_token_count=412,
            content_token_count=87,
            model_used="llama3.1:8b",
        )

        messages = collector.flush()   # returns accumulated list and resets
    """

    _instance: TraceCollector | None = None
    _class_lock: threading.Lock = threading.Lock()

    # ── Singleton construction ────────────────────────────────────────────────

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._messages: list[AgentMessage] = []
        self._current_trace_id: UUID | None = None

    @classmethod
    def get_instance(cls) -> TraceCollector:
        """Return the process-wide singleton, creating it if necessary."""
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """
        Destroy the current singleton and clear all accumulated state.

        Primarily used in tests to guarantee isolation between test cases.
        """
        with cls._class_lock:
            if cls._instance is not None:
                with cls._instance._lock:
                    cls._instance._messages = []
                    cls._instance._current_trace_id = None
            cls._instance = None

    # ── Run lifecycle ─────────────────────────────────────────────────────────

    def start_run(self, trace_id: UUID) -> None:
        """
        Begin accumulating messages for a new run identified by *trace_id*.

        Clears any previously accumulated messages so the collector is ready
        for a fresh recording session.
        """
        with self._lock:
            self._current_trace_id = trace_id
            self._messages = []

    # ── Recording ─────────────────────────────────────────────────────────────

    def record(
        self,
        source_agent: str,
        target_agent: str,
        content: str,
        message_type: MessageType,
        api_token_count: int | None = None,
        content_token_count: int | None = None,
        model_used: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """
        Record one inter-agent message.

        ``message_index`` is set automatically based on the current accumulated
        count — callers should not pass it explicitly.

        Returns the constructed ``AgentMessage`` so callers can inspect it if
        needed (e.g. for unit tests or framework-specific post-processing).
        """
        with self._lock:
            index = len(self._messages)
            msg = AgentMessage(
                trace_id=self._current_trace_id,
                message_index=index,
                timestamp=datetime.utcnow(),
                source_agent=source_agent,
                target_agent=target_agent,
                content=content,
                message_type=message_type,
                api_token_count=api_token_count,
                content_token_count=content_token_count,
                model_used=model_used,
                tool_calls=tool_calls if tool_calls is not None else [],
                metadata=metadata if metadata is not None else {},
            )
            self._messages.append(msg)
        return msg

    # ── State access ──────────────────────────────────────────────────────────

    def get_messages(self) -> list[AgentMessage]:
        """Return a snapshot of currently accumulated messages (does not reset)."""
        with self._lock:
            return list(self._messages)

    def flush(self) -> list[AgentMessage]:
        """
        Return all accumulated messages and reset internal state.

        The caller (typically the framework runner) is responsible for
        persisting the returned messages to PostgreSQL.
        """
        with self._lock:
            messages = list(self._messages)
            self._messages = []
            self._current_trace_id = None
        return messages


# ── Module-level convenience accessor ────────────────────────────────────────


def get_collector() -> TraceCollector:
    """Return the process-wide TraceCollector singleton."""
    return TraceCollector.get_instance()
