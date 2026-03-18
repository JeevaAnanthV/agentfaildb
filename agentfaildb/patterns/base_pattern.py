"""
Abstract base class for all failure pattern detectors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from agentfaildb.trace import (
    AgentMessage,
    AnnotationSource,
    FailureAnnotation,
    FailureCategory,
    FailureSeverity,
    TaskTrace,
)


class BasePattern(ABC):
    """
    Abstract base class for AgentFailDB failure pattern detectors.

    Each concrete subclass implements detect() and returns a list of
    FailureAnnotations (empty list if no failure is detected).
    """

    @abstractmethod
    def detect(self, trace: TaskTrace) -> list[FailureAnnotation]:
        """
        Analyse the trace and return any detected FailureAnnotations.

        Returns an empty list if no failure of this type is detected.
        """

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _content_messages(self, trace: TaskTrace) -> list[AgentMessage]:
        """Return only content-bearing messages (used in failure detection)."""
        return [m for m in trace.messages if m.message_type.is_content]

    def _make_annotation(
        self,
        trace_id: UUID | None,
        category: FailureCategory,
        severity: FailureSeverity,
        description: str,
        confidence: float,
        root_cause_agent: str | None = None,
        failure_point: int | None = None,
    ) -> FailureAnnotation:
        """Convenience factory for creating FailureAnnotation objects."""
        return FailureAnnotation(
            trace_id=trace_id,
            category=category,
            severity=severity,
            description=description,
            confidence=min(1.0, max(0.0, confidence)),
            source=AnnotationSource.RULE_BASED,
            root_cause_agent=root_cause_agent,
            failure_point_index=failure_point,
        )
