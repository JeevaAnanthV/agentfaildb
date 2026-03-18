"""
Role violation pattern detector.

Checks whether any agent performs actions outside its declared role by
comparing message content against role descriptions using keyword matching
and sentence-transformer embeddings.

If content similarity to another agent's role exceeds similarity to the
agent's own role by margin > 0.3, flags ROLE_VIOLATION.

sentence-transformers model is loaded lazily on first use.
"""

from __future__ import annotations

import logging
from typing import Any

from agentfaildb.patterns.base_pattern import BasePattern
from agentfaildb.trace import FailureCategory, FailureSeverity, TaskTrace

logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"
_VIOLATION_MARGIN = 0.3

# Module-level lazy-loaded model
_embedding_model: Any = None


def _get_model() -> Any:
    """Lazily load the sentence-transformers model on first call."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        _embedding_model = SentenceTransformer(_MODEL_NAME)
    return _embedding_model


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    import math

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class RoleViolationPattern(BasePattern):
    """Detect agents performing work outside their declared role boundaries."""

    def detect(self, trace: TaskTrace) -> list["FailureAnnotation"]:  # noqa: F821
        messages = self._content_messages(trace)
        agent_roles = trace.agent_roles  # canonical_role → description

        if len(agent_roles) < 2 or not messages:
            return []

        # Filter to RESPONSE messages only — these represent actual agent outputs
        response_msgs = [
            m for m in messages
            if m.message_type.value == "response"
        ]

        if not response_msgs:
            return []

        # Try embedding-based detection first
        try:
            return self._detect_with_embeddings(trace, response_msgs, agent_roles)
        except Exception as embed_exc:
            logger.warning("Embedding-based role detection failed, falling back to keywords: %s", embed_exc)
            return self._detect_with_keywords(trace, response_msgs, agent_roles)

    def _detect_with_embeddings(
        self,
        trace: TaskTrace,
        response_msgs: list,
        agent_roles: dict[str, str],
    ) -> list:
        model = _get_model()

        # Embed all role descriptions
        roles = list(agent_roles.keys())
        role_descs = [agent_roles[r] for r in roles]
        role_embeddings = model.encode(role_descs).tolist()

        annotations = []

        for msg in response_msgs:
            source = msg.source_agent
            if source not in agent_roles:
                continue

            # Embed the message content
            content_embedding = model.encode([msg.content[:1000]])[0].tolist()

            # Similarity to own role
            own_idx = roles.index(source)
            own_similarity = _cosine_similarity(content_embedding, role_embeddings[own_idx])

            # Similarity to other roles
            for other_idx, other_role in enumerate(roles):
                if other_role == source:
                    continue
                other_similarity = _cosine_similarity(content_embedding, role_embeddings[other_idx])

                if other_similarity - own_similarity > _VIOLATION_MARGIN:
                    severity = FailureSeverity.MINOR
                    if other_similarity - own_similarity > 0.5:
                        severity = FailureSeverity.MAJOR

                    confidence = min(
                        0.85,
                        0.5 + (other_similarity - own_similarity - _VIOLATION_MARGIN) * 0.5,
                    )

                    description = (
                        f"Role violation: agent '{source}' (role: {agent_roles.get(source, '')[:60]}) "
                        f"produced content more similar to '{other_role}' role "
                        f"(similarity {other_similarity:.2f} vs own role {own_similarity:.2f}, "
                        f"margin {other_similarity - own_similarity:.2f})."
                    )

                    annotations.append(
                        self._make_annotation(
                            trace_id=trace.trace_id,
                            category=FailureCategory.ROLE_VIOLATION,
                            severity=severity,
                            description=description,
                            confidence=confidence,
                            root_cause_agent=source,
                            failure_point=msg.message_index,
                        )
                    )
                    # Only report the worst violation per message
                    break

        return annotations

    def _detect_with_keywords(
        self,
        trace: TaskTrace,
        response_msgs: list,
        agent_roles: dict[str, str],
    ) -> list:
        """
        Keyword-based fallback.

        Each role description is split into keywords; agent messages are
        checked against all role keyword sets.
        """
        role_keywords: dict[str, set[str]] = {}
        for role, desc in agent_roles.items():
            words = set(desc.lower().split())
            # Add role name itself as keyword
            words.update(role.lower().split("_"))
            role_keywords[role] = words

        annotations = []
        for msg in response_msgs:
            source = msg.source_agent
            if source not in role_keywords:
                continue

            content_words = set(msg.content.lower().split())
            own_overlap = len(content_words & role_keywords[source])

            for other_role, other_kw in role_keywords.items():
                if other_role == source:
                    continue
                other_overlap = len(content_words & other_kw)

                if other_overlap > own_overlap * 2 and other_overlap > 5:
                    description = (
                        f"Keyword-based role violation: agent '{source}' "
                        f"message overlaps more with '{other_role}' keywords "
                        f"({other_overlap} vs {own_overlap})."
                    )
                    annotations.append(
                        self._make_annotation(
                            trace_id=trace.trace_id,
                            category=FailureCategory.ROLE_VIOLATION,
                            severity=FailureSeverity.MINOR,
                            description=description,
                            confidence=0.4,
                            root_cause_agent=source,
                            failure_point=msg.message_index,
                        )
                    )
                    break

        return annotations
