"""
AgentFailDB Interactive Leaderboard — Streamlit application.

Displays:
  - Failure rate by framework (bar chart)
  - Failure rate by category (heatmap)
  - Resource exhaustion comparison (box plot)
  - Top 10 most-failed tasks (table)

Data is fetched from PostgreSQL directly (or from analysis_results/metrics.json as fallback).

Launch:
  streamlit run agentfaildb/leaderboard/app.py
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Streamlit app entry point ─────────────────────────────────────────────────


def main() -> None:
    import streamlit as st

    st.set_page_config(
        page_title="AgentFailDB Leaderboard",
        page_icon=None,
        layout="wide",
    )

    st.title("AgentFailDB — Multi-Agent Failure Mode Leaderboard")
    st.caption(
        "Benchmarking failure modes in CrewAI, AutoGen, LangGraph, and MetaGPT "
        "across 50 tasks in 5 categories."
    )

    # ── Sidebar filters ───────────────────────────────────────────────────────
    st.sidebar.header("Filters")

    framework_options = ["All", "crewai", "autogen", "langgraph", "metagpt"]
    selected_framework = st.sidebar.selectbox("Framework", framework_options)
    framework_filter = None if selected_framework == "All" else selected_framework

    category_options = [
        "All",
        "collaborative_research",
        "code_generation",
        "debate_reasoning",
        "planning",
        "data_analysis",
    ]
    selected_category = st.sidebar.selectbox("Task Category", category_options)
    category_filter = None if selected_category == "All" else selected_category

    difficulty_options = ["All", "easy", "medium", "hard", "adversarial"]
    st.sidebar.selectbox("Difficulty", difficulty_options)

    # ── Data loading ──────────────────────────────────────────────────────────
    data = _load_data(framework_filter, category_filter)

    if data is None:
        st.warning(
            "No data available. Run some benchmark tasks first, or ensure PostgreSQL is running."
        )
        _show_placeholder()
        return

    # ── Main content ──────────────────────────────────────────────────────────
    _show_summary_metrics(data, st)
    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        _show_failure_rate_by_framework(data, st)
    with col2:
        _show_failure_category_distribution(data, st)

    st.divider()

    col3, col4 = st.columns(2)
    with col3:
        _show_heatmap(data, st)
    with col4:
        _show_resource_exhaustion(data, st)

    st.divider()
    _show_top_failed_tasks(data, st)


def _load_data(
    framework_filter: str | None,
    category_filter: str | None,
) -> dict[str, Any] | None:
    """
    Load metrics data from PostgreSQL or fallback to metrics.json.
    """
    # Try PostgreSQL first
    try:
        from agentfaildb.harness.db import Database  # noqa: PLC0415
        from agentfaildb.metrics import (  # noqa: PLC0415
            compute_failure_rates,
            compute_resource_exhaustion_baselines,
        )

        db = Database()
        db.connect()
        data = {
            "failure_rates": compute_failure_rates(db, framework=framework_filter),
            "baselines": compute_resource_exhaustion_baselines(db),
            "raw_traces": db.get_traces_for_analysis(
                framework=framework_filter,
                task_category=category_filter,
                limit=500,
            ),
        }
        db.disconnect()

        if data["failure_rates"]["total_traces"] == 0:
            return None
        return data

    except Exception as exc:
        logger.warning("PostgreSQL unavailable, trying metrics.json: %s", exc)

    # Fallback to metrics.json
    metrics_path = Path("analysis_results/metrics.json")
    if metrics_path.exists():
        try:
            with open(metrics_path, encoding="utf-8") as f:
                cached = json.load(f)
            return {
                "failure_rates": cached.get("failure_rates", {}),
                "baselines": cached.get("resource_exhaustion_baselines", {}),
                "raw_traces": [],
            }
        except Exception as exc:
            logger.warning("Failed to load metrics.json: %s", exc)

    return None


def _show_summary_metrics(data: dict[str, Any], st: Any) -> None:
    """Display top-level summary KPI cards."""
    fr = data["failure_rates"]
    total = fr.get("total_traces", 0)
    by_fw = fr.get("by_framework", {})
    by_cat = fr.get("by_category", {})

    overall_failure_rate = 0.0
    if by_cat:
        # Any trace with at least one annotation counts as failed
        any_failure = sum(v.get("count", 0) for v in by_cat.values())
        overall_failure_rate = any_failure / max(total, 1)

    best_fw = ""
    best_rate = 1.0
    for fw, fw_data in by_fw.items():
        rate = fw_data.get("failure_rate", 1.0)
        if rate < best_rate:
            best_rate = rate
            best_fw = fw

    worst_cat = ""
    worst_rate = 0.0
    for cat, cat_data in by_cat.items():
        rate = cat_data.get("rate", 0.0)
        if rate > worst_rate:
            worst_rate = rate
            worst_cat = cat

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Traces", f"{total:,}")
    col2.metric("Overall Failure Rate", f"{overall_failure_rate:.1%}")
    col3.metric("Best Framework", f"{best_fw} ({best_rate:.1%})" if best_fw else "N/A")
    col4.metric("Most Common Failure", f"{worst_cat} ({worst_rate:.1%})" if worst_cat else "N/A")


def _show_failure_rate_by_framework(data: dict[str, Any], st: Any) -> None:
    """Bar chart: failure rate by framework."""
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError:
        st.info("pandas required for charts")
        return

    by_fw = data["failure_rates"].get("by_framework", {})
    if not by_fw:
        st.info("No framework data available")
        return

    df = pd.DataFrame([
        {"Framework": fw, "Failure Rate (%)": fw_data.get("failure_rate", 0) * 100}
        for fw, fw_data in by_fw.items()
    ]).sort_values("Failure Rate (%)", ascending=False)

    st.subheader("Failure Rate by Framework")
    st.bar_chart(df.set_index("Framework")["Failure Rate (%)"])


def _show_failure_category_distribution(data: dict[str, Any], st: Any) -> None:
    """Bar chart: overall failure rate per category."""
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError:
        st.info("pandas required for charts")
        return

    by_cat = data["failure_rates"].get("by_category", {})
    if not by_cat:
        st.info("No category data available")
        return

    df = pd.DataFrame([
        {"Category": cat, "Rate (%)": v.get("rate", 0) * 100}
        for cat, v in by_cat.items()
        if cat != "none"
    ]).sort_values("Rate (%)", ascending=False)

    st.subheader("Failure Rate by Category")
    st.bar_chart(df.set_index("Category")["Rate (%)"])


def _show_heatmap(data: dict[str, Any], st: Any) -> None:
    """Heatmap: framework × failure category rates."""
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError:
        st.info("pandas required for heatmap")
        return

    by_fw_cat = data["failure_rates"].get("by_framework_category", {})
    if not by_fw_cat:
        st.info("No heatmap data available")
        return

    df = pd.DataFrame(by_fw_cat).T * 100  # frameworks as rows, categories as columns
    df = df.round(1)

    st.subheader("Failure Rate Heatmap (%) — Framework x Category")
    st.dataframe(
        df.style.background_gradient(cmap="Reds", axis=None),
        use_container_width=True,
    )


def _show_resource_exhaustion(data: dict[str, Any], st: Any) -> None:
    """Summary table of resource exhaustion baselines."""
    baselines = data.get("baselines", {})

    st.subheader("Resource Exhaustion Baselines")

    if not baselines:
        st.info("No baseline data available (requires traces in DB)")
        return

    try:
        import pandas as pd  # noqa: PLC0415

        rows = []
        for key, vals in baselines.items():
            category, difficulty = key.split(":", 1) if ":" in key else (key, "unknown")
            rows.append({
                "Category": category,
                "Difficulty": difficulty,
                "Median Tokens": int(vals.get("tokens", 0)),
                "Median Time (s)": round(vals.get("time_s", 0), 1),
                "Median Messages": int(vals.get("messages", 0)),
            })

        df = pd.DataFrame(rows).sort_values(["Category", "Difficulty"])
        st.dataframe(df, use_container_width=True)
    except ImportError:
        st.json(baselines)


def _show_top_failed_tasks(data: dict[str, Any], st: Any) -> None:
    """Table: top 10 tasks by failure rate."""
    raw_traces = data.get("raw_traces", [])

    st.subheader("Top 10 Most-Failed Tasks")

    if not raw_traces:
        st.info("No trace data available for task-level analysis")
        return

    try:
        import agentfaildb.harness.db  # noqa: PLC0415, F401
    except ImportError:
        st.info("Database unavailable")
        return

    # Count failures per task_id
    task_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "failed": 0, "category": "", "difficulty": ""}
    )

    for trace in raw_traces:
        tid = trace.task_id
        task_stats[tid]["total"] += 1
        task_stats[tid]["category"] = trace.task_category
        task_stats[tid]["difficulty"] = trace.task_difficulty
        if trace.task_success is False:
            task_stats[tid]["failed"] += 1

    rows = []
    for task_id, stats in task_stats.items():
        total = stats["total"]
        failed = stats["failed"]
        rows.append({
            "Task ID": task_id,
            "Category": stats["category"],
            "Difficulty": stats["difficulty"],
            "Total Runs": total,
            "Failed Runs": failed,
            "Failure Rate": f"{failed / max(total, 1):.1%}",
        })

    rows_sorted = sorted(rows, key=lambda r: r["Failed Runs"], reverse=True)[:10]

    if not rows_sorted:
        st.info("No task failure data available")
        return

    try:
        import pandas as pd  # noqa: PLC0415
        df = pd.DataFrame(rows_sorted)
        st.dataframe(df, use_container_width=True)
    except ImportError:
        st.table(rows_sorted)


def _show_placeholder() -> None:
    """Show placeholder content when no data is available."""
    import streamlit as st

    st.info(
        "To populate the leaderboard:\n\n"
        "1. Start PostgreSQL and Redis: `docker-compose up -d`\n"
        "2. Run some benchmark tasks: `python -m agentfaildb.harness.orchestrator`\n"
        "3. Refresh this page"
    )


if __name__ == "__main__":
    main()
