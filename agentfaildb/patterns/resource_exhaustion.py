"""
Resource exhaustion pattern detector.

Compares a trace's token count, elapsed time, and message count against
baseline medians. If any metric exceeds 3× the baseline, flags the trace.

Severity:
  3–5× above baseline: MINOR
  5–10× above baseline: MAJOR
  >10× above baseline: CRITICAL

Two modes:
  online  — baselines fetched from RedisClient
  post-hoc — baselines provided as a dict at construction time
"""

from __future__ import annotations

import logging
from typing import Any

from agentfaildb.patterns.base_pattern import BasePattern
from agentfaildb.trace import FailureCategory, FailureSeverity, TaskTrace

logger = logging.getLogger(__name__)


def _default_baselines() -> dict[str, Any]:
    """
    Return default baselines, pulling time_s from BASELINE_TIME_SECONDS env var
    if set. This allows local Ollama deployments (slow GPU) to calibrate the
    resource-exhaustion threshold without changing code.

    Default: tokens=10000, time_s=120.0 (target API baseline), messages=30.
    Set BASELINE_TIME_SECONDS=600 for local Ollama on GTX 1650.
    """
    import os
    time_s = float(os.environ.get("BASELINE_TIME_SECONDS", "120.0"))
    tokens = int(os.environ.get("BASELINE_TOKENS", "10000"))
    messages = int(os.environ.get("BASELINE_MESSAGES", "30"))
    return {"tokens": tokens, "time_s": time_s, "messages": messages}


# Module-level constant kept for backward compat; use _default_baselines() in code.
_DEFAULT_BASELINES: dict[str, Any] = {
    "tokens": 10_000,
    "time_s": 120.0,
    "messages": 30,
}


def _severity_for_ratio(ratio: float) -> FailureSeverity:
    if ratio > 10.0:
        return FailureSeverity.CRITICAL
    elif ratio > 5.0:
        return FailureSeverity.MAJOR
    else:
        return FailureSeverity.MINOR


class ResourceExhaustionPattern(BasePattern):
    """Detect resource exhaustion by comparing metrics to baselines."""

    _THRESHOLD_MULTIPLIER = 3.0

    def __init__(
        self,
        redis_client: Any = None,
        baselines: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialise with optional Redis client or static baselines dict.

        If redis_client is provided, baselines are fetched dynamically per
        (category, difficulty). If baselines dict is provided, it is used
        directly. If neither, hard-coded defaults are used.
        """
        self._redis = redis_client
        self._static_baselines = baselines

    def detect(self, trace: TaskTrace) -> list["FailureAnnotation"]:  # noqa: F821
        baseline = self._get_baseline(trace.task_category, trace.task_difficulty)

        worst_ratio = 0.0
        worst_metric = ""

        # Check tokens
        if trace.total_api_tokens > 0:
            token_ratio = trace.total_api_tokens / max(baseline["tokens"], 1)
            if token_ratio >= self._THRESHOLD_MULTIPLIER:
                if token_ratio > worst_ratio:
                    worst_ratio = token_ratio
                    worst_metric = "tokens"

        # Check time
        if trace.total_time_seconds > 0:
            time_ratio = trace.total_time_seconds / max(baseline["time_s"], 1)
            if time_ratio >= self._THRESHOLD_MULTIPLIER:
                if time_ratio > worst_ratio:
                    worst_ratio = time_ratio
                    worst_metric = "time"

        # Check message count
        msg_count = len(trace.messages)
        if msg_count > 0:
            msg_ratio = msg_count / max(baseline["messages"], 1)
            if msg_ratio >= self._THRESHOLD_MULTIPLIER:
                if msg_ratio > worst_ratio:
                    worst_ratio = msg_ratio
                    worst_metric = "messages"

        if worst_ratio < self._THRESHOLD_MULTIPLIER:
            return []

        severity = _severity_for_ratio(worst_ratio)
        confidence = min(0.9, 0.6 + (worst_ratio - self._THRESHOLD_MULTIPLIER) * 0.03)

        description = (
            f"Resource exhaustion detected: {worst_metric} exceeded baseline by "
            f"{worst_ratio:.1f}×. "
            f"Tokens: {trace.total_api_tokens} (baseline {baseline['tokens']}), "
            f"Time: {trace.total_time_seconds:.1f}s (baseline {baseline['time_s']}s), "
            f"Messages: {msg_count} (baseline {baseline['messages']})."
        )

        annotation = self._make_annotation(
            trace_id=trace.trace_id,
            category=FailureCategory.RESOURCE_EXHAUSTION,
            severity=severity,
            description=description,
            confidence=confidence,
        )
        return [annotation]

    def _get_baseline(self, category: str, difficulty: str) -> dict[str, Any]:
        """Fetch baseline from Redis, static dict, or hard-coded defaults."""
        if self._redis is not None:
            try:
                return self._redis.get_baseline(category, difficulty)
            except Exception as exc:
                logger.warning("Redis baseline fetch failed: %s", exc)

        if self._static_baselines is not None:
            # Static baselines may be keyed as flat or nested by category/difficulty
            key = f"{category}:{difficulty}"
            if key in self._static_baselines:
                return self._static_baselines[key]
            # Fall back to top-level if present
            if "tokens" in self._static_baselines:
                return self._static_baselines

        return _default_baselines()
