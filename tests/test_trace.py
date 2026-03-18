"""
Tests for agentfaildb/trace.py — data model correctness.
"""

from __future__ import annotations


import pytest
from pydantic import ValidationError

from agentfaildb.trace import (
    AgentMessage,
    AnnotationSource,
    FailureAnnotation,
    FailureCategory,
    FailureSeverity,
    GroundTruthType,
    MessageType,
    TaskTrace,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_trace(**overrides) -> TaskTrace:
    defaults = dict(
        framework="crewai",
        task_category="collaborative_research",
        task_difficulty="medium",
        task_id="task_001",
        task_description="Research and summarise quantum computing",
        ground_truth_type=GroundTruthType.CLAIM_LIST,
        num_agents=2,
        agent_roles={"researcher": "Gathers information", "writer": "Writes report"},
        model_used="llama3.1:8b",
    )
    defaults.update(overrides)
    return TaskTrace(**defaults)


def _make_message(index: int = 0, **overrides) -> AgentMessage:
    defaults = dict(
        message_index=index,
        source_agent="researcher",
        target_agent="writer",
        content="Here is what I found.",
        message_type=MessageType.RESPONSE,
    )
    defaults.update(overrides)
    return AgentMessage(**defaults)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestAgentMessageCreation:
    def test_agent_message_creation(self) -> None:
        msg = _make_message(
            index=0,
            api_token_count=300,
            content_token_count=50,
            model_used="llama3.1:8b",
        )

        assert msg.message_index == 0
        assert msg.source_agent == "researcher"
        assert msg.target_agent == "writer"
        assert msg.content == "Here is what I found."
        assert msg.message_type == MessageType.RESPONSE
        assert msg.api_token_count == 300
        assert msg.content_token_count == 50
        assert msg.model_used == "llama3.1:8b"
        # Auto-generated fields
        assert msg.message_id is not None
        assert msg.timestamp is not None
        assert msg.tool_calls == []
        assert msg.metadata == {}


class TestMessageTypeIsContent:
    @pytest.mark.parametrize(
        "msg_type,expected",
        [
            (MessageType.TASK_DELEGATION, True),
            (MessageType.RESPONSE, True),
            (MessageType.FEEDBACK, True),
            (MessageType.TOOL_CALL, True),
            (MessageType.TOOL_RESULT, True),
            (MessageType.SYSTEM_CONTROL, False),
            (MessageType.SUBSCRIPTION_ROUTING, False),
            (MessageType.INTERNAL_REASONING, False),
            (MessageType.CHECKPOINT, False),
        ],
    )
    def test_message_type_is_content(self, msg_type: MessageType, expected: bool) -> None:
        assert msg_type.is_content is expected


class TestTaskTraceComputedProperties:
    def test_task_trace_computed_properties(self) -> None:
        """
        Three messages: two with token counts, one without.
        Verify that totals and ratio use only the messages where counts are set.
        """
        msg1 = _make_message(0, api_token_count=400, content_token_count=100)
        msg2 = _make_message(1, api_token_count=600, content_token_count=150)
        msg3 = _make_message(2)  # no token counts

        trace = _make_trace(messages=[msg1, msg2, msg3])

        assert trace.total_api_tokens == 1000
        assert trace.total_content_tokens == 250
        assert trace.context_overhead_ratio == pytest.approx(4.0)

    def test_context_overhead_ratio_zero_when_no_content_tokens(self) -> None:
        trace = _make_trace(messages=[_make_message(0)])
        assert trace.context_overhead_ratio == 0.0


class TestTaskTraceContentMessagesFilter:
    def test_task_trace_content_messages_filter(self) -> None:
        content_msg = _make_message(0, message_type=MessageType.RESPONSE)
        artifact_msg = _make_message(1, message_type=MessageType.CHECKPOINT)
        tool_msg = _make_message(2, message_type=MessageType.TOOL_CALL)
        internal_msg = _make_message(3, message_type=MessageType.INTERNAL_REASONING)

        trace = _make_trace(messages=[content_msg, artifact_msg, tool_msg, internal_msg])

        content = trace.content_messages
        artifacts = trace.artifact_messages

        assert len(content) == 2
        assert content_msg in content
        assert tool_msg in content

        assert len(artifacts) == 2
        assert artifact_msg in artifacts
        assert internal_msg in artifacts


class TestFailureAnnotationConfidenceValidation:
    def test_valid_confidence_boundaries(self) -> None:
        for val in (0.0, 0.5, 1.0):
            ann = FailureAnnotation(
                category=FailureCategory.NONE,
                severity=FailureSeverity.NONE,
                description="No failure detected",
                confidence=val,
                source=AnnotationSource.RULE_BASED,
            )
            assert ann.confidence == val

    def test_confidence_below_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            FailureAnnotation(
                category=FailureCategory.NONE,
                severity=FailureSeverity.NONE,
                description="test",
                confidence=-0.1,
                source=AnnotationSource.RULE_BASED,
            )

    def test_confidence_above_one_raises(self) -> None:
        with pytest.raises(ValidationError):
            FailureAnnotation(
                category=FailureCategory.DELEGATION_LOOP,
                severity=FailureSeverity.CRITICAL,
                description="test",
                confidence=1.01,
                source=AnnotationSource.LLM_OLLAMA,
            )


class TestTraceToDbDict:
    def test_trace_to_db_dict(self) -> None:
        trace = _make_trace(
            messages=[_make_message(0, api_token_count=100, content_token_count=40)],
            annotations=[
                FailureAnnotation(
                    category=FailureCategory.NONE,
                    severity=FailureSeverity.NONE,
                    description="clean",
                    confidence=1.0,
                    source=AnnotationSource.RULE_BASED,
                )
            ],
        )

        db_dict = trace.to_db_dict()

        # Must not contain the transient list fields
        assert "messages" not in db_dict
        assert "annotations" not in db_dict

        # Must contain all traces-table columns
        expected_keys = {
            "trace_id",
            "framework",
            "task_category",
            "task_difficulty",
            "task_id",
            "task_description",
            "ground_truth_type",
            "ground_truth",
            "actual_output",
            "total_api_tokens",
            "total_content_tokens",
            "context_overhead_ratio",
            "total_time_seconds",
            "num_agents",
            "agent_roles",
            "task_success",
            "task_score",
            "task_success_method",
            "model_used",
            "run_timestamp",
            "run_config",
        }
        assert expected_keys.issubset(db_dict.keys())

        # Verify computed fields are included with correct values
        assert db_dict["total_api_tokens"] == 100
        assert db_dict["total_content_tokens"] == 40
        assert db_dict["context_overhead_ratio"] == pytest.approx(2.5)
