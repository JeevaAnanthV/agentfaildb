"""
Context degradation pattern detector.

Measures information retention across the message chain.

Detection logic:
  1. Embed the first RESPONSE message and the last RESPONSE message.
     If cosine similarity < 0.4 → flag CONTEXT_DEGRADATION.
  2. Extract key terms from the task description. Check if they appear in
     early messages but not in the final output. If many key terms are
     dropped (> 50% of top-10 task terms), flag as additional evidence.

sentence-transformers model loaded lazily on first use.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agentfaildb.patterns.base_pattern import BasePattern
from agentfaildb.trace import FailureCategory, FailureSeverity, TaskTrace

logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"
_SIMILARITY_THRESHOLD = 0.4
_KEY_TERM_DROP_RATIO = 0.5

# Lazy-loaded model shared across pattern instances
_embedding_model: Any = None


def _get_model() -> Any:
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        _embedding_model = SentenceTransformer(_MODEL_NAME)
    return _embedding_model


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "that", "this", "these",
    "those", "it", "its", "not", "no", "if", "as", "so", "than", "then",
    "also", "all", "any", "both", "each", "more", "other", "such", "into",
    "through", "during", "about", "above", "between", "out", "off", "over",
}


def _extract_key_terms(text: str, top_n: int = 10) -> list[str]:
    """Extract top_n most significant (non-stopword) terms from text."""
    words = re.findall(r"\b[a-zA-Z]{4,}\b", text.lower())
    from collections import Counter
    counts = Counter(w for w in words if w not in _STOP_WORDS)
    return [term for term, _ in counts.most_common(top_n)]


class ContextDegradationPattern(BasePattern):
    """Detect loss of task context across the agent message chain."""

    def detect(self, trace: TaskTrace) -> list["FailureAnnotation"]:  # noqa: F821
        messages = self._content_messages(trace)

        response_msgs = [m for m in messages if m.message_type.value == "response"]
        if len(response_msgs) < 2:
            return []

        first_msg = response_msgs[0]
        last_msg = response_msgs[-1]

        # ── Embedding similarity check ────────────────────────────────────────
        similarity_flag = False
        similarity_score = 1.0

        try:
            model = _get_model()
            embeddings = model.encode([first_msg.content[:1000], last_msg.content[:1000]])
            similarity_score = float(_cosine_similarity(embeddings[0].tolist(), embeddings[1].tolist()))
            if similarity_score < _SIMILARITY_THRESHOLD:
                similarity_flag = True
        except Exception as embed_exc:
            logger.warning("Context degradation embedding failed: %s", embed_exc)

        # ── Key term retention check ──────────────────────────────────────────
        key_terms = _extract_key_terms(trace.task_description, top_n=10)
        term_drop_flag = False
        dropped_terms: list[str] = []

        if key_terms:
            early_content = " ".join(m.content for m in response_msgs[:max(1, len(response_msgs) // 2)]).lower()
            final_content = last_msg.content.lower()

            early_present = [t for t in key_terms if t in early_content]
            dropped_terms = [t for t in early_present if t not in final_content]

            if early_present and (len(dropped_terms) / len(early_present)) > _KEY_TERM_DROP_RATIO:
                term_drop_flag = True

        if not similarity_flag and not term_drop_flag:
            return []

        # Determine severity
        if similarity_flag and similarity_score < 0.2:
            severity = FailureSeverity.CRITICAL
        elif similarity_flag and similarity_score < 0.3:
            severity = FailureSeverity.MAJOR
        else:
            severity = FailureSeverity.MINOR

        confidence = 0.5
        if similarity_flag:
            confidence += (1.0 - max(similarity_score, 0.0)) * 0.3
        if term_drop_flag and dropped_terms:
            confidence += (len(dropped_terms) / max(len(key_terms), 1)) * 0.2
        confidence = min(0.9, confidence)

        description_parts = []
        if similarity_flag:
            description_parts.append(
                f"Semantic similarity between first and last RESPONSE messages is "
                f"{similarity_score:.2f} (threshold {_SIMILARITY_THRESHOLD})."
            )
        if term_drop_flag:
            description_parts.append(
                f"Key task terms dropped from early to final messages: "
                f"{dropped_terms[:5]}."
            )

        description = "Context degradation detected. " + " ".join(description_parts)

        annotation = self._make_annotation(
            trace_id=trace.trace_id,
            category=FailureCategory.CONTEXT_DEGRADATION,
            severity=severity,
            description=description,
            confidence=confidence,
            failure_point=first_msg.message_index,
        )
        return [annotation]
