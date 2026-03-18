"""
Core data model for AgentFailDB.

Every inter-agent message and every failure annotation is normalised into the
classes defined here regardless of which framework produced them.  All enum
values match the PostgreSQL enum types in db/init.sql exactly.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, computed_field, field_validator


# ── Enums ────────────────────────────────────────────────────────────────────


class FailureCategory(str, Enum):
    CASCADING_HALLUCINATION = "cascading_hallucination"
    DELEGATION_LOOP = "delegation_loop"
    CONTEXT_DEGRADATION = "context_degradation"
    CONFLICTING_OUTPUTS = "conflicting_outputs"
    ROLE_VIOLATION = "role_violation"
    SILENT_FAILURE = "silent_failure"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    NONE = "none"


class FailureSeverity(str, Enum):
    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class GroundTruthType(str, Enum):
    DETERMINISTIC = "deterministic"
    CLAIM_LIST = "claim_list"
    RUBRIC = "rubric"


class MessageType(str, Enum):
    # ── Content types — included in failure detection ────────────────────────
    TASK_DELEGATION = "task_delegation"
    RESPONSE = "response"
    FEEDBACK = "feedback"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    # ── Artifact types — excluded from detection by default ──────────────────
    SYSTEM_CONTROL = "system_control"
    SUBSCRIPTION_ROUTING = "subscription_routing"
    INTERNAL_REASONING = "internal_reasoning"
    CHECKPOINT = "checkpoint"

    @property
    def is_content(self) -> bool:
        """True for content-bearing message types used in failure detection."""
        return self in {
            MessageType.TASK_DELEGATION,
            MessageType.RESPONSE,
            MessageType.FEEDBACK,
            MessageType.TOOL_CALL,
            MessageType.TOOL_RESULT,
        }


class AnnotationSource(str, Enum):
    RULE_BASED = "rule_based"
    LLM_OLLAMA = "llm_ollama"
    LLM_CLAUDE = "llm_claude"
    HUMAN = "human"


# ── AgentMessage ─────────────────────────────────────────────────────────────


class AgentMessage(BaseModel):
    """
    A single normalised inter-agent message captured during a framework run.

    api_token_count  — taken from the LLM API response's usage metadata.
    content_token_count — estimated via tiktoken over the content field.
    """

    model_config = ConfigDict(populate_by_name=True)

    message_id: UUID = None  # type: ignore[assignment]
    trace_id: UUID | None = None
    message_index: int
    timestamp: datetime = None  # type: ignore[assignment]
    source_agent: str
    target_agent: str
    content: str
    message_type: MessageType
    api_token_count: int | None = None
    content_token_count: int | None = None
    model_used: str | None = None
    tool_calls: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        if self.message_id is None:
            object.__setattr__(self, "message_id", uuid4())
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", datetime.utcnow())


# ── FailureAnnotation ─────────────────────────────────────────────────────────


class FailureAnnotation(BaseModel):
    """An annotation produced by a pattern detector, LLM annotator, or human."""

    model_config = ConfigDict(populate_by_name=True)

    annotation_id: UUID = None  # type: ignore[assignment]
    trace_id: UUID | None = None
    category: FailureCategory
    severity: FailureSeverity
    root_cause_agent: str | None = None
    failure_point_index: int | None = None
    description: str
    confidence: float
    source: AnnotationSource
    annotator_id: str | None = None
    created_at: datetime = None  # type: ignore[assignment]

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {v}")
        return v

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        if self.annotation_id is None:
            object.__setattr__(self, "annotation_id", uuid4())
        if self.created_at is None:
            object.__setattr__(self, "created_at", datetime.utcnow())


# ── TaskTrace ─────────────────────────────────────────────────────────────────


class TaskTrace(BaseModel):
    """
    Complete record of one framework run: task metadata, all inter-agent
    messages, computed token metrics, and any failure annotations.
    """

    model_config = ConfigDict(populate_by_name=True)

    trace_id: UUID = None  # type: ignore[assignment]
    framework: str
    task_category: str
    task_difficulty: str
    task_id: str
    task_description: str
    ground_truth_type: GroundTruthType
    ground_truth: dict[str, Any] | None = None
    actual_output: str = ""

    messages: list[AgentMessage] = []
    annotations: list[FailureAnnotation] = []

    total_time_seconds: float = 0.0
    num_agents: int
    agent_roles: dict[str, str]  # role_name -> description

    task_success: bool | None = None
    task_score: float | None = None
    task_success_method: str | None = None

    model_used: str
    run_timestamp: datetime = None  # type: ignore[assignment]
    run_config: dict[str, Any] = {}

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        if self.trace_id is None:
            object.__setattr__(self, "trace_id", uuid4())
        if self.run_timestamp is None:
            object.__setattr__(self, "run_timestamp", datetime.utcnow())

    # ── Computed token metrics ────────────────────────────────────────────────

    @computed_field  # type: ignore[misc]
    @property
    def total_api_tokens(self) -> int:
        """Sum of api_token_count for all messages where it is set."""
        return sum(m.api_token_count for m in self.messages if m.api_token_count is not None)

    @computed_field  # type: ignore[misc]
    @property
    def total_content_tokens(self) -> int:
        """Sum of content_token_count for all messages where it is set."""
        return sum(
            m.content_token_count for m in self.messages if m.content_token_count is not None
        )

    @computed_field  # type: ignore[misc]
    @property
    def context_overhead_ratio(self) -> float:
        """
        Ratio of API tokens to content tokens.

        A value > 1.0 indicates that the context window carries more tokens
        than the raw message content (system prompts, conversation history,
        tool schemas, etc.).  Returns 0.0 when content tokens are unknown.
        """
        if self.total_content_tokens > 0:
            return self.total_api_tokens / self.total_content_tokens
        return 0.0

    # ── Message filters ───────────────────────────────────────────────────────

    @property
    def content_messages(self) -> list[AgentMessage]:
        """Messages whose type is a content type (used in failure detection)."""
        return [m for m in self.messages if m.message_type.is_content]

    @property
    def artifact_messages(self) -> list[AgentMessage]:
        """Messages whose type is an artifact type (excluded from detection)."""
        return [m for m in self.messages if not m.message_type.is_content]

    # ── DB serialisation ──────────────────────────────────────────────────────

    def to_db_dict(self) -> dict[str, Any]:
        """
        Return a flat dict mapping to the columns of the ``traces`` table.

        Deliberately excludes the transient ``messages`` and ``annotations``
        lists — those are written via separate INSERT statements.
        """
        return {
            "trace_id": self.trace_id,
            "framework": self.framework,
            "task_category": self.task_category,
            "task_difficulty": self.task_difficulty,
            "task_id": self.task_id,
            "task_description": self.task_description,
            "ground_truth_type": self.ground_truth_type.value,
            "ground_truth": self.ground_truth,
            "actual_output": self.actual_output,
            "total_api_tokens": self.total_api_tokens,
            "total_content_tokens": self.total_content_tokens,
            "context_overhead_ratio": self.context_overhead_ratio,
            "total_time_seconds": self.total_time_seconds,
            "num_agents": self.num_agents,
            "agent_roles": self.agent_roles,
            "task_success": self.task_success,
            "task_score": self.task_score,
            "task_success_method": self.task_success_method,
            "model_used": self.model_used,
            "run_timestamp": self.run_timestamp,
            "run_config": self.run_config,
        }
