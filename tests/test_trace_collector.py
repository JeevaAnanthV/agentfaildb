"""
Tests for agentfaildb/harness/trace_collector.py — singleton collector behaviour.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agentfaildb.harness.trace_collector import TraceCollector, get_collector
from agentfaildb.trace import MessageType


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_collector():
    """
    Ensure a clean singleton for every test.

    autouse=True means this runs automatically before (and after, via yield)
    each test in this module.
    """
    TraceCollector.reset()
    yield
    TraceCollector.reset()


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestSingletonPattern:
    def test_singleton_pattern(self) -> None:
        """Two calls to get_collector() must return the exact same object."""
        c1 = get_collector()
        c2 = get_collector()
        assert c1 is c2

    def test_get_instance_equals_get_collector(self) -> None:
        """TraceCollector.get_instance() and get_collector() are equivalent."""
        assert TraceCollector.get_instance() is get_collector()


class TestRecordAndFlush:
    def test_record_and_flush(self) -> None:
        """Record 3 messages, flush, verify count and that state is reset."""
        collector = get_collector()
        trace_id = uuid4()
        collector.start_run(trace_id)

        for i in range(3):
            collector.record(
                source_agent=f"agent_{i}",
                target_agent="orchestrator",
                content=f"Message {i}",
                message_type=MessageType.RESPONSE,
            )

        messages = collector.flush()

        assert len(messages) == 3
        # After flush the collector must be empty
        assert collector.get_messages() == []

    def test_flush_returns_messages_in_order(self) -> None:
        collector = get_collector()
        collector.start_run(uuid4())

        collector.record("a", "b", "first", MessageType.TASK_DELEGATION)
        collector.record("b", "c", "second", MessageType.RESPONSE)

        messages = collector.flush()
        assert messages[0].content == "first"
        assert messages[1].content == "second"

    def test_flush_sets_trace_id_on_messages(self) -> None:
        trace_id = uuid4()
        collector = get_collector()
        collector.start_run(trace_id)
        collector.record("a", "b", "hello", MessageType.FEEDBACK)

        messages = collector.flush()
        assert messages[0].trace_id == trace_id


class TestMessageIndexAutoIncrement:
    def test_message_index_auto_increment(self) -> None:
        """message_index must start at 0 and increment by 1 per recorded message."""
        collector = get_collector()
        collector.start_run(uuid4())

        for _ in range(5):
            collector.record(
                source_agent="agent_a",
                target_agent="agent_b",
                content="ping",
                message_type=MessageType.TASK_DELEGATION,
            )

        messages = collector.get_messages()
        indices = [m.message_index for m in messages]
        assert indices == list(range(5))

    def test_start_run_resets_index(self) -> None:
        """Calling start_run() a second time must reset the index counter."""
        collector = get_collector()

        collector.start_run(uuid4())
        collector.record("a", "b", "first run msg", MessageType.RESPONSE)
        assert collector.get_messages()[0].message_index == 0

        # Start a new run — index should restart from 0
        collector.start_run(uuid4())
        collector.record("a", "b", "second run msg", MessageType.RESPONSE)
        assert collector.get_messages()[0].message_index == 0


class TestResetClearsState:
    def test_reset_clears_state(self) -> None:
        """After reset(), get_messages() must return an empty list."""
        collector = get_collector()
        collector.start_run(uuid4())
        collector.record("a", "b", "something", MessageType.TOOL_CALL)

        # Sanity check: there is a message before reset
        assert len(collector.get_messages()) == 1

        TraceCollector.reset()

        # A fresh singleton must have no messages
        fresh = get_collector()
        assert fresh.get_messages() == []

    def test_reset_creates_new_instance(self) -> None:
        """After reset(), get_collector() returns a different object."""
        original = get_collector()
        TraceCollector.reset()
        new_instance = get_collector()
        assert original is not new_instance


class TestOptionalRecordFields:
    def test_record_with_all_optional_fields(self) -> None:
        collector = get_collector()
        collector.start_run(uuid4())

        msg = collector.record(
            source_agent="llm_agent",
            target_agent="tool_agent",
            content='{"query": "search term"}',
            message_type=MessageType.TOOL_CALL,
            api_token_count=512,
            content_token_count=20,
            model_used="llama3.1:8b",
            tool_calls=[{"name": "web_search", "args": {"q": "search term"}}],
            metadata={"latency_ms": 340},
        )

        assert msg.api_token_count == 512
        assert msg.content_token_count == 20
        assert msg.model_used == "llama3.1:8b"
        assert msg.tool_calls[0]["name"] == "web_search"
        assert msg.metadata["latency_ms"] == 340

    def test_record_defaults_for_optional_fields(self) -> None:
        collector = get_collector()
        collector.start_run(uuid4())

        msg = collector.record("a", "b", "hi", MessageType.RESPONSE)

        assert msg.api_token_count is None
        assert msg.content_token_count is None
        assert msg.model_used is None
        assert msg.tool_calls == []
        assert msg.metadata == {}
