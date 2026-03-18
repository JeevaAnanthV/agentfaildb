"""
Conflicting outputs pattern detector.

Finds pairs of RESPONSE messages from different agents that are
semantically dissimilar (< 0.3 cosine similarity) AND both address
the same aspect of the task (both have high overlap with task description).

Severity based on the number of conflicting pairs found.
"""

from __future__ import annotations

import logging
from itertools import combinations
from typing import Any

from agentfaildb.patterns.base_pattern import BasePattern
from agentfaildb.trace import FailureCategory, FailureSeverity, TaskTrace

logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"
_CONFLICT_SIMILARITY_THRESHOLD = 0.3
_TASK_RELEVANCE_THRESHOLD = 0.25

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


class ConflictingOutputsPattern(BasePattern):
    """Detect semantically conflicting outputs from different agents."""

    def detect(self, trace: TaskTrace) -> list["FailureAnnotation"]:  # noqa: F821
        messages = self._content_messages(trace)
        response_msgs = [m for m in messages if m.message_type.value == "response"]

        # Need at least 2 responses from different agents
        if len(response_msgs) < 2:
            return []

        # Only compare messages from different agents
        agent_to_msgs: dict[str, list] = {}
        for msg in response_msgs:
            agent_to_msgs.setdefault(msg.source_agent, []).append(msg)

        if len(agent_to_msgs) < 2:
            return []

        # One representative message per agent (last RESPONSE for each)
        rep_msgs = {agent: msgs[-1] for agent, msgs in agent_to_msgs.items()}
        agents = list(rep_msgs.keys())

        try:
            return self._detect_with_embeddings(trace, rep_msgs, agents)
        except Exception as exc:
            logger.warning("Conflicting outputs embedding failed: %s", exc)
            return self._detect_with_keyword_overlap(trace, rep_msgs, agents)

    def _detect_with_embeddings(
        self,
        trace: TaskTrace,
        rep_msgs: dict[str, Any],
        agents: list[str],
    ) -> list:
        model = _get_model()

        # Encode task description + all representative messages
        texts = [trace.task_description[:500]] + [
            rep_msgs[a].content[:1000] for a in agents
        ]
        embeddings = model.encode(texts).tolist()
        task_embedding = embeddings[0]
        msg_embeddings = {agents[i]: embeddings[i + 1] for i in range(len(agents))}

        conflicts = []
        for a1, a2 in combinations(agents, 2):
            e1 = msg_embeddings[a1]
            e2 = msg_embeddings[a2]

            # Check mutual similarity (low = potential conflict)
            mutual_sim = _cosine_similarity(e1, e2)
            if mutual_sim >= _CONFLICT_SIMILARITY_THRESHOLD:
                continue

            # Both must be relevant to the task
            task_sim_1 = _cosine_similarity(e1, task_embedding)
            task_sim_2 = _cosine_similarity(e2, task_embedding)
            if (
                task_sim_1 < _TASK_RELEVANCE_THRESHOLD
                or task_sim_2 < _TASK_RELEVANCE_THRESHOLD
            ):
                continue

            conflicts.append((a1, a2, mutual_sim))

        if not conflicts:
            return []

        n = len(conflicts)
        if n >= 3:
            severity = FailureSeverity.CRITICAL
        elif n == 2:
            severity = FailureSeverity.MAJOR
        else:
            severity = FailureSeverity.MINOR

        confidence = min(0.85, 0.55 + n * 0.1)
        worst_pair = min(conflicts, key=lambda x: x[2])

        description = (
            f"Conflicting outputs detected: {n} conflicting agent pair(s). "
            f"Worst conflict: {worst_pair[0]} vs {worst_pair[1]} "
            f"(similarity {worst_pair[2]:.2f}). "
            f"Both agents addressed the task but produced incompatible outputs."
        )

        msg_index = rep_msgs[worst_pair[0]].message_index

        annotation = self._make_annotation(
            trace_id=trace.trace_id,
            category=FailureCategory.CONFLICTING_OUTPUTS,
            severity=severity,
            description=description,
            confidence=confidence,
            root_cause_agent=worst_pair[0],
            failure_point=msg_index,
        )
        return [annotation]

    def _detect_with_keyword_overlap(
        self,
        trace: TaskTrace,
        rep_msgs: dict[str, Any],
        agents: list[str],
    ) -> list:
        """
        Fallback: compare word sets between agent outputs.

        If two agents share < 10% vocabulary overlap but both overlap > 10%
        with task description keywords, flag as conflicting.
        """
        import re

        def keywords(text: str) -> set[str]:
            return set(re.findall(r"\b[a-zA-Z]{4,}\b", text.lower()))

        task_words = keywords(trace.task_description)
        conflicts = []

        for a1, a2 in combinations(agents, 2):
            w1 = keywords(rep_msgs[a1].content)
            w2 = keywords(rep_msgs[a2].content)

            shared = w1 & w2
            union = w1 | w2
            jaccard = len(shared) / max(len(union), 1)

            if jaccard >= 0.1:
                continue

            t1 = len(w1 & task_words) / max(len(task_words), 1)
            t2 = len(w2 & task_words) / max(len(task_words), 1)
            if t1 < 0.1 or t2 < 0.1:
                continue

            conflicts.append((a1, a2, jaccard))

        if not conflicts:
            return []

        description = (
            f"Keyword-based conflicting outputs: {len(conflicts)} pairs with "
            f"< 10% vocabulary overlap while both addressing the task."
        )

        annotation = self._make_annotation(
            trace_id=trace.trace_id,
            category=FailureCategory.CONFLICTING_OUTPUTS,
            severity=FailureSeverity.MINOR,
            description=description,
            confidence=0.4,
        )
        return [annotation]
