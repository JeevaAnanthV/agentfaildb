"""
Cascading hallucination pattern detector.

Detects claims that propagate across multiple agent messages without
grounding in the task description or ground truth.

Method:
  1. Extract n-gram "claims" (5-gram to 10-gram spans) from each RESPONSE message.
  2. Filter to suspicious claims: those NOT present in the task description
     or ground truth (using substring overlap check).
  3. Track how many subsequent agent messages repeat each suspicious claim.
  4. If a suspicious claim appears in 3+ agent messages: flag CASCADING_HALLUCINATION.

Severity:
  Claim in 3 messages: MINOR
  Claim in 4–5 messages: MAJOR
  Claim in 6+ messages: CRITICAL
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from agentfaildb.patterns.base_pattern import BasePattern
from agentfaildb.trace import FailureCategory, FailureSeverity, TaskTrace

_NGRAM_SIZE_MIN = 5
_NGRAM_SIZE_MAX = 10
_PROPAGATION_THRESHOLD = 3


def _normalise(text: str) -> str:
    """Lowercase and strip punctuation for comparison."""
    return re.sub(r"[^a-z0-9\s]", "", text.lower())


def _extract_ngrams(text: str, n_min: int, n_max: int) -> list[str]:
    """Extract all n-grams of length n_min to n_max from text."""
    words = _normalise(text).split()
    ngrams = []
    for n in range(n_min, n_max + 1):
        for i in range(len(words) - n + 1):
            ngrams.append(" ".join(words[i : i + n]))
    return ngrams


def _is_grounded(ngram: str, reference_texts: list[str]) -> bool:
    """Return True if the n-gram appears in any reference text."""
    ngram_norm = _normalise(ngram)
    for ref in reference_texts:
        if ngram_norm in _normalise(ref):
            return True
    return False


class CascadingHallucinationPattern(BasePattern):
    """Detect claims that propagate across agent messages without grounding."""

    def detect(self, trace: TaskTrace) -> list["FailureAnnotation"]:  # noqa: F821
        messages = self._content_messages(trace)
        response_msgs = [m for m in messages if m.message_type.value == "response"]

        if len(response_msgs) < _PROPAGATION_THRESHOLD:
            return []

        # Build reference corpus: task description + ground truth text
        reference_texts = [trace.task_description]
        if trace.ground_truth:
            if "claims" in trace.ground_truth:
                for c in trace.ground_truth["claims"]:
                    reference_texts.append(c.get("claim", ""))
            if "assertions" in trace.ground_truth:
                for a in trace.ground_truth["assertions"]:
                    reference_texts.append(a.get("test", ""))

        # Map: ngram → list of (message_index, agent_name) where it appears
        ngram_appearances: dict[str, list[tuple[int, str]]] = defaultdict(list)

        for msg in response_msgs:
            ngrams = _extract_ngrams(msg.content, _NGRAM_SIZE_MIN, _NGRAM_SIZE_MAX)
            seen_in_this_msg: set[str] = set()
            for ng in ngrams:
                if ng not in seen_in_this_msg and not _is_grounded(ng, reference_texts):
                    ngram_appearances[ng].append((msg.message_index, msg.source_agent))
                    seen_in_this_msg.add(ng)

        # Find suspicious claims: appear in ≥ PROPAGATION_THRESHOLD different messages
        suspicious: list[tuple[str, list[tuple[int, str]]]] = []
        for ng, appearances in ngram_appearances.items():
            # Count distinct message indices
            distinct_messages = len({idx for idx, _ in appearances})
            if distinct_messages >= _PROPAGATION_THRESHOLD:
                suspicious.append((ng, appearances))

        if not suspicious:
            return []

        # Find worst case
        worst_ng, worst_appearances = max(suspicious, key=lambda x: len({i for i, _ in x[1]}))
        distinct_count = len({idx for idx, _ in worst_appearances})

        if distinct_count >= 6:
            severity = FailureSeverity.CRITICAL
        elif distinct_count >= 4:
            severity = FailureSeverity.MAJOR
        else:
            severity = FailureSeverity.MINOR

        confidence = min(0.85, 0.5 + (distinct_count - _PROPAGATION_THRESHOLD) * 0.1)
        first_idx = min(idx for idx, _ in worst_appearances)
        first_agent = next(agent for idx, agent in worst_appearances if idx == first_idx)

        description = (
            f"Cascading hallucination detected: {len(suspicious)} ungrounded claim(s) "
            f"propagated across {distinct_count}+ messages. "
            f"Example claim: '{worst_ng[:80]}...' originated in message {first_idx} "
            f"by agent '{first_agent}'."
        )

        annotation = self._make_annotation(
            trace_id=trace.trace_id,
            category=FailureCategory.CASCADING_HALLUCINATION,
            severity=severity,
            description=description,
            confidence=confidence,
            root_cause_agent=first_agent,
            failure_point=first_idx,
        )
        return [annotation]
