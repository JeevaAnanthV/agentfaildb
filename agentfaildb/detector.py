"""
Central failure detector for AgentFailDB.

FailureDetector runs all pattern modules against a TaskTrace and returns
a consolidated list of FailureAnnotations.

Rules:
  - SilentFailurePattern only fires if no other pattern produced confidence > 0.6.
  - Redis is used for caching pattern signatures when available.
  - verbose=True includes INTERNAL_REASONING messages for role violation checks.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from agentfaildb.patterns.cascading_hallucination import CascadingHallucinationPattern
from agentfaildb.patterns.conflicting_outputs import ConflictingOutputsPattern
from agentfaildb.patterns.context_degradation import ContextDegradationPattern
from agentfaildb.patterns.delegation_loop import DelegationLoopPattern
from agentfaildb.patterns.resource_exhaustion import ResourceExhaustionPattern
from agentfaildb.patterns.role_violation import RoleViolationPattern
from agentfaildb.patterns.silent_failure import SilentFailurePattern
from agentfaildb.trace import FailureAnnotation, MessageType, TaskTrace

logger = logging.getLogger(__name__)

_SILENT_FAILURE_CONFIDENCE_GATE = 0.6
_CACHE_TTL = 3600


class FailureDetector:
    """
    Runs all failure pattern detectors against a TaskTrace.

    Args:
        redis_client:  Optional RedisClient for pattern signature caching
                       and resource-exhaustion baselines.
        baselines:     Optional dict of baseline metrics for post-hoc
                       ResourceExhaustionPattern (used when Redis unavailable).
        evaluator:     Optional GroundTruthEvaluator for SilentFailurePattern.
    """

    def __init__(
        self,
        redis_client: Any = None,
        baselines: dict[str, Any] | None = None,
        evaluator: Any = None,
    ) -> None:
        self._redis = redis_client
        self._silent_pattern = SilentFailurePattern(evaluator=evaluator)
        self._patterns = [
            DelegationLoopPattern(),
            ResourceExhaustionPattern(redis_client=redis_client, baselines=baselines),
            RoleViolationPattern(),
            ContextDegradationPattern(),
            ConflictingOutputsPattern(),
            CascadingHallucinationPattern(),
        ]

    def analyze(self, trace: TaskTrace, verbose: bool = False) -> list[FailureAnnotation]:
        """
        Run all pattern detectors against the trace.

        verbose=True includes INTERNAL_REASONING messages in the trace
        passed to RoleViolationPattern (other patterns use content_messages).

        SilentFailurePattern only fires if no other pattern produced
        an annotation with confidence > _SILENT_FAILURE_CONFIDENCE_GATE.

        Results are cached in Redis by trace signature when available.
        """
        # Check Redis cache first
        cache_key = self._trace_signature(trace)
        if self._redis is not None:
            try:
                cached = self._redis.get_pattern_signature(cache_key)
                if cached is not None:
                    logger.debug("Cache hit for trace %s", trace.trace_id)
                    return self._deserialise_annotations(cached, trace)
            except Exception as cache_exc:
                logger.warning("Redis cache read failed: %s", cache_exc)

        # Filter trace messages for verbose mode
        if verbose:
            # Pass full trace — patterns that need only content_messages
            # call self._content_messages() internally
            analysis_trace = trace
        else:
            # Exclude INTERNAL_REASONING from the trace copy
            filtered_messages = [
                m for m in trace.messages if m.message_type != MessageType.INTERNAL_REASONING
            ]
            analysis_trace = trace.model_copy(update={"messages": filtered_messages})

        all_annotations: list[FailureAnnotation] = []

        # Run main patterns
        for pattern in self._patterns:
            try:
                annotations = pattern.detect(analysis_trace)
                all_annotations.extend(annotations)
            except Exception as exc:
                logger.warning(
                    "Pattern %s failed on trace %s: %s",
                    pattern.__class__.__name__,
                    trace.trace_id,
                    exc,
                )

        # Run SilentFailure only if no high-confidence annotations found
        high_confidence_found = any(
            a.confidence > _SILENT_FAILURE_CONFIDENCE_GATE for a in all_annotations
        )
        if not high_confidence_found:
            try:
                silent_annotations = self._silent_pattern.detect(analysis_trace)
                all_annotations.extend(silent_annotations)
            except Exception as exc:
                logger.warning(
                    "SilentFailurePattern failed on trace %s: %s",
                    trace.trace_id,
                    exc,
                )

        # Attach trace_id to all annotations
        for annotation in all_annotations:
            if annotation.trace_id is None:
                object.__setattr__(annotation, "trace_id", trace.trace_id)

        # Cache results in Redis
        if self._redis is not None and all_annotations:
            try:
                self._redis.cache_pattern_signature(
                    cache_key,
                    self._serialise_annotations(all_annotations),
                    ttl=_CACHE_TTL,
                )
            except Exception as cache_exc:
                logger.warning("Redis cache write failed: %s", cache_exc)

        return all_annotations

    # Keep the old spelling as an alias for backward compatibility
    def analyse(self, trace: TaskTrace, verbose: bool = False) -> list[FailureAnnotation]:
        """Alias for analyze() — British English spelling."""
        return self.analyze(trace, verbose=verbose)

    # ── Cache serialisation ───────────────────────────────────────────────────

    @staticmethod
    def _trace_signature(trace: TaskTrace) -> str:
        """Compute a stable cache key from the trace's content."""
        sig = f"{trace.trace_id}:{len(trace.messages)}:{trace.total_api_tokens}"
        return hashlib.md5(sig.encode()).hexdigest()  # noqa: S324

    @staticmethod
    def _serialise_annotations(annotations: list[FailureAnnotation]) -> dict[str, Any]:
        return {"annotations": [a.model_dump(mode="json") for a in annotations]}

    @staticmethod
    def _deserialise_annotations(data: dict[str, Any], trace: TaskTrace) -> list[FailureAnnotation]:

        results = []
        for item in data.get("annotations", []):
            try:
                ann = FailureAnnotation(**item)
                results.append(ann)
            except Exception as exc:
                logger.warning("Failed to deserialise cached annotation: %s", exc)
        return results
