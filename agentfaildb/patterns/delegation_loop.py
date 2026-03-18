"""
Delegation loop pattern detector.

Detects when the same (source → target) pair repeats excessively,
indicating agents are bouncing work back and forth without progress.

Thresholds:
  ≥3 consecutive or ≥5 total occurrences of the same pair: flag
  3–4 total: MINOR
  5–7 total: MAJOR
  8+: CRITICAL
"""

from __future__ import annotations

from collections import Counter

from agentfaildb.patterns.base_pattern import BasePattern
from agentfaildb.trace import FailureCategory, FailureSeverity, TaskTrace


class DelegationLoopPattern(BasePattern):
    """Detect repeated (source → target) delegation pairs."""

    _CONSECUTIVE_THRESHOLD = 3
    _TOTAL_THRESHOLD = 5

    def detect(self, trace: TaskTrace) -> list["FailureAnnotation"]:  # noqa: F821
        messages = self._content_messages(trace)

        if len(messages) < self._CONSECUTIVE_THRESHOLD:
            return []

        pairs = [(m.source_agent, m.target_agent) for m in messages]

        # Check for consecutive repetitions
        max_consecutive, consecutive_pair, first_consecutive_idx = self._max_consecutive(pairs)
        # Check total counts
        pair_counts: Counter[tuple[str, str]] = Counter(pairs)
        max_total = pair_counts.most_common(1)[0][1] if pair_counts else 0
        top_pair = pair_counts.most_common(1)[0][0] if pair_counts else None

        # Determine if we should flag
        consecutive_trigger = max_consecutive >= self._CONSECUTIVE_THRESHOLD
        total_trigger = max_total >= self._TOTAL_THRESHOLD

        if not consecutive_trigger and not total_trigger:
            return []

        # Use whichever triggered with higher severity
        if total_trigger and top_pair is not None:
            total_count = max_total
        else:
            total_count = max_consecutive
            top_pair = consecutive_pair

        # Severity tiers
        if total_count >= 8:
            severity = FailureSeverity.CRITICAL
        elif total_count >= 5:
            severity = FailureSeverity.MAJOR
        else:
            severity = FailureSeverity.MINOR

        # Find first occurrence index
        failure_idx = None
        if top_pair is not None:
            for i, p in enumerate(pairs):
                if p == top_pair:
                    failure_idx = messages[i].message_index
                    break

        # Use consecutive index if that's what triggered
        if consecutive_trigger and first_consecutive_idx is not None:
            failure_idx = messages[first_consecutive_idx].message_index

        confidence = min(0.95, 0.6 + (total_count - self._CONSECUTIVE_THRESHOLD) * 0.05)
        source_agent = top_pair[0] if top_pair else None

        description = (
            f"Delegation loop detected: pair ({top_pair[0]} → {top_pair[1]}) "
            f"appeared {total_count} times "
            f"({'consecutive' if consecutive_trigger else 'total'}). "
            f"Severity: {severity.value}."
        )

        annotation = self._make_annotation(
            trace_id=trace.trace_id,
            category=FailureCategory.DELEGATION_LOOP,
            severity=severity,
            description=description,
            confidence=confidence,
            root_cause_agent=source_agent,
            failure_point=failure_idx,
        )
        return [annotation]

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _max_consecutive(
        pairs: list[tuple[str, str]],
    ) -> tuple[int, tuple[str, str] | None, int | None]:
        """
        Return (max_run_length, pair, first_index_of_run).
        """
        if not pairs:
            return 0, None, None

        max_run = 1
        current_run = 1
        best_pair: tuple[str, str] = pairs[0]
        best_start: int = 0
        current_start: int = 0

        for i in range(1, len(pairs)):
            if pairs[i] == pairs[i - 1]:
                current_run += 1
                if current_run > max_run:
                    max_run = current_run
                    best_pair = pairs[i]
                    best_start = current_start
            else:
                current_run = 1
                current_start = i

        return max_run, best_pair, best_start
