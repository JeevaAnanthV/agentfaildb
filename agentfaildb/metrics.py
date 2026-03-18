"""
Metrics computation functions for AgentFailDB.

Functions:
  compute_failure_rates         — per-framework / per-category failure rates
  compute_per_category_kappa    — Cohen's Kappa between human and LLM annotations
  compute_macro_kappa           — macro-average Kappa across categories
  compute_resource_exhaustion_baselines — median baselines from DB
  compute_context_overhead_stats — context overhead ratio statistics
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from agentfaildb.harness.db import Database
from agentfaildb.trace import FailureCategory

logger = logging.getLogger(__name__)

_FRAMEWORKS = ["crewai", "autogen", "langgraph", "metagpt"]
_CATEGORIES = [c.value for c in FailureCategory if c != FailureCategory.NONE]


# ── Failure rates ─────────────────────────────────────────────────────────────


def compute_failure_rates(
    db: Database,
    framework: str | None = None,
) -> dict[str, Any]:
    """
    Compute failure rates grouped by framework and failure category.

    Returns a dict with keys:
      total_traces, by_framework, by_category, by_framework_category
    """
    traces = db.get_traces_for_analysis(framework=framework, limit=2000)

    if not traces:
        return {
            "total_traces": 0,
            "by_framework": {},
            "by_category": {},
            "by_framework_category": {},
        }

    # Collect annotation data per trace
    framework_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    category_counts: dict[str, int] = defaultdict(int)
    total_by_framework: dict[str, int] = defaultdict(int)

    for trace in traces:
        fw = trace.framework
        total_by_framework[fw] += 1

        annotations = db.get_annotations(trace.trace_id)
        seen_categories: set[str] = set()
        for ann in annotations:
            cat = ann.category.value
            if cat != "none" and cat not in seen_categories:
                framework_counts[fw][cat] += 1
                category_counts[cat] += 1
                seen_categories.add(cat)

    # Build result
    by_framework: dict[str, Any] = {}
    for fw in set(_FRAMEWORKS) | set(framework_counts.keys()):
        total = total_by_framework.get(fw, 0)
        if total == 0:
            continue
        failure_total = sum(framework_counts[fw].values())
        by_framework[fw] = {
            "total_traces": total,
            "failure_count": failure_total,
            "failure_rate": failure_total / total,
            "by_category": {
                cat: {
                    "count": framework_counts[fw].get(cat, 0),
                    "rate": framework_counts[fw].get(cat, 0) / total,
                }
                for cat in _CATEGORIES
            },
        }

    total_traces = len(traces)
    by_category: dict[str, Any] = {
        cat: {
            "count": category_counts.get(cat, 0),
            "rate": category_counts.get(cat, 0) / max(total_traces, 1),
        }
        for cat in _CATEGORIES
    }

    # Cross-table: framework × category failure rates
    by_framework_category: dict[str, dict[str, float]] = {}
    for fw, fw_data in by_framework.items():
        by_framework_category[fw] = {
            cat: fw_data["by_category"][cat]["rate"] for cat in _CATEGORIES
        }

    return {
        "total_traces": total_traces,
        "by_framework": by_framework,
        "by_category": by_category,
        "by_framework_category": by_framework_category,
    }


# ── Cohen's Kappa ─────────────────────────────────────────────────────────────


def compute_per_category_kappa(
    human_annotations: list[dict[str, Any]],
    llm_annotations: list[dict[str, Any]],
) -> dict[str, float]:
    """
    Compute Cohen's Kappa between human and LLM annotations per category.

    Input format: each annotation is a dict with at minimum:
      {"trace_id": str, "category": str}

    Returns dict mapping category → kappa score.
    """
    # Index by trace_id
    human_by_trace: dict[str, set[str]] = defaultdict(set)
    llm_by_trace: dict[str, set[str]] = defaultdict(set)

    for ann in human_annotations:
        human_by_trace[ann["trace_id"]].add(ann["category"])

    for ann in llm_annotations:
        llm_by_trace[ann["trace_id"]].add(ann["category"])

    all_trace_ids = set(human_by_trace.keys()) | set(llm_by_trace.keys())
    if not all_trace_ids:
        return {cat: 0.0 for cat in _CATEGORIES}

    kappas: dict[str, float] = {}
    for category in _CATEGORIES:
        # Binary labels per trace
        human_labels = [int(category in human_by_trace.get(tid, set())) for tid in all_trace_ids]
        llm_labels = [int(category in llm_by_trace.get(tid, set())) for tid in all_trace_ids]

        kappas[category] = _cohens_kappa(human_labels, llm_labels)

    return kappas


def compute_macro_kappa(per_category_kappas: dict[str, float]) -> float:
    """Compute the macro-average Kappa across all categories."""
    values = list(per_category_kappas.values())
    if not values:
        return 0.0
    return sum(values) / len(values)


def _cohens_kappa(labels_a: list[int], labels_b: list[int]) -> float:
    """Compute Cohen's Kappa for two binary label sequences."""
    n = len(labels_a)
    if n == 0:
        return 0.0

    # Observed agreement
    agree = sum(1 for a, b in zip(labels_a, labels_b) if a == b)
    p_o = agree / n

    # Expected agreement
    pa_1 = sum(labels_a) / n
    pb_1 = sum(labels_b) / n
    pa_0 = 1.0 - pa_1
    pb_0 = 1.0 - pb_1
    p_e = pa_1 * pb_1 + pa_0 * pb_0

    if p_e >= 1.0:
        return 1.0
    return (p_o - p_e) / (1.0 - p_e)


# ── Resource exhaustion baselines ─────────────────────────────────────────────


def compute_resource_exhaustion_baselines(
    db: Database,
) -> dict[str, dict[str, float]]:
    """
    Compute median token/time/message baselines from stored traces.

    Returns a dict keyed as "category:difficulty" → {"tokens": ..., "time_s": ..., "messages": ...}
    """
    traces = db.get_traces_for_analysis(limit=2000)

    # Group by category:difficulty
    groups: dict[str, list[dict[str, float]]] = defaultdict(list)
    for trace in traces:
        key = f"{trace.task_category}:{trace.task_difficulty}"
        groups[key].append(
            {
                "tokens": trace.total_api_tokens,
                "time_s": trace.total_time_seconds,
                "messages": len(trace.messages),
            }
        )

    baselines: dict[str, dict[str, float]] = {}
    for key, samples in groups.items():
        if len(samples) < 3:
            continue

        tokens_sorted = sorted(s["tokens"] for s in samples)
        time_sorted = sorted(s["time_s"] for s in samples)
        msgs_sorted = sorted(s["messages"] for s in samples)

        mid = len(samples) // 2
        baselines[key] = {
            "tokens": float(tokens_sorted[mid]),
            "time_s": float(time_sorted[mid]),
            "messages": float(msgs_sorted[mid]),
        }

    return baselines


# ── Context overhead stats ────────────────────────────────────────────────────


def compute_context_overhead_stats(db: Database) -> dict[str, Any]:
    """
    Compute context_overhead_ratio statistics from stored traces.

    Returns mean, median, p75, p90, and max per framework.
    """
    traces = db.get_traces_for_analysis(limit=2000)

    by_framework: dict[str, list[float]] = defaultdict(list)
    for trace in traces:
        if trace.context_overhead_ratio > 0:
            by_framework[trace.framework].append(trace.context_overhead_ratio)

    stats: dict[str, Any] = {}
    for fw, ratios in by_framework.items():
        if not ratios:
            continue
        sorted_r = sorted(ratios)
        n = len(sorted_r)
        stats[fw] = {
            "count": n,
            "mean": sum(sorted_r) / n,
            "median": sorted_r[n // 2],
            "p75": sorted_r[int(n * 0.75)],
            "p90": sorted_r[int(n * 0.90)],
            "max": sorted_r[-1],
        }

    return stats
