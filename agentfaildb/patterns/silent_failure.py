"""
Silent confident failure pattern detector.

Fires when a task appears to fail (low score) but no other failure pattern
was detected with confidence > 0.6. This is the most dangerous failure mode:
the system confidently produces a wrong or incomplete answer with no signals.

Tier-specific thresholds:
  Tier 1 (DETERMINISTIC): score < 0.4
  Tier 2 (CLAIM_LIST):    score < 0.4
  Tier 3 (RUBRIC):        score < 2.0

Severity: always CRITICAL.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agentfaildb.patterns.base_pattern import BasePattern
from agentfaildb.trace import FailureCategory, FailureSeverity, GroundTruthType, TaskTrace

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_TIER1_SCORE_THRESHOLD = 0.4
_TIER2_SCORE_THRESHOLD = 0.4
_TIER3_SCORE_THRESHOLD = 2.0


class SilentFailurePattern(BasePattern):
    """
    Detect silent confident failures using ground truth evaluation.

    Requires an evaluator instance to score the trace output.
    """

    def __init__(self, evaluator: Any = None) -> None:
        """
        Initialise with an optional GroundTruthEvaluator instance.

        If None, a new GroundTruthEvaluator is created on first use.
        """
        self._evaluator = evaluator

    def detect(self, trace: TaskTrace) -> list["FailureAnnotation"]:  # noqa: F821
        """
        Detect silent failure.

        NOTE: This pattern is intended to be called by FailureDetector only
        when no other pattern has fired with confidence > 0.6.
        """
        if trace.ground_truth is None:
            return []

        if not trace.actual_output or not trace.actual_output.strip():
            # Empty output — this would be caught by resource_exhaustion or other patterns
            return []

        evaluator = self._get_evaluator()

        try:
            _, score, _ = evaluator.evaluate(trace)
        except Exception as exc:
            logger.warning("SilentFailurePattern evaluator failed: %s", exc)
            return []

        gtt = trace.ground_truth_type
        threshold = self._get_threshold(gtt)

        if score >= threshold:
            return []

        confidence = min(0.9, 0.6 + (threshold - score) * 0.5)

        description = (
            f"Silent confident failure: task score {score:.2f} is below threshold "
            f"{threshold} for {gtt.value} ground truth, but no other failure pattern "
            f"was detected. The system appeared to complete the task without obvious "
            f"error signals despite poor output quality."
        )

        annotation = self._make_annotation(
            trace_id=trace.trace_id,
            category=FailureCategory.SILENT_FAILURE,
            severity=FailureSeverity.CRITICAL,
            description=description,
            confidence=confidence,
        )
        return [annotation]

    def _get_threshold(self, gtt: GroundTruthType) -> float:
        if gtt == GroundTruthType.RUBRIC:
            return _TIER3_SCORE_THRESHOLD
        elif gtt == GroundTruthType.CLAIM_LIST:
            return _TIER2_SCORE_THRESHOLD
        else:
            return _TIER1_SCORE_THRESHOLD

    def _get_evaluator(self) -> Any:
        if self._evaluator is None:
            from agentfaildb.evaluator import GroundTruthEvaluator  # noqa: PLC0415
            self._evaluator = GroundTruthEvaluator()
        return self._evaluator
