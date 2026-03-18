"""
HuggingFace dataset exporter for AgentFailDB.

Reads all traces + annotations from PostgreSQL, writes Parquet files to
data/hf_export/ in the format expected by the AgentFailDB HuggingFace dataset.

Usage:
    python -m agentfaildb.analysis.export_hf_dataset
    python -m agentfaildb.analysis.export_hf_dataset --split 80/20
    python -m agentfaildb.analysis.export_hf_dataset --out-dir /tmp/hf_export
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _export(out_dir: Path, train_frac: float = 0.8) -> None:
    try:
        import pandas as pd  # type: ignore[import]
    except ImportError:
        raise SystemExit(
            "pandas is required for HF export. Install with: pip install pandas pyarrow"
        )

    from agentfaildb.harness.db import Database

    out_dir.mkdir(parents=True, exist_ok=True)

    db = Database()
    db.connect()

    try:
        traces = db.get_traces_for_analysis(limit=100_000)
    finally:
        db.disconnect()

    if not traces:
        logger.warning("No traces found in database — nothing to export.")
        return

    logger.info("Exporting %d traces…", len(traces))

    # ── Build flat records ─────────────────────────────────────────────────────
    trace_rows: list[dict[str, Any]] = []
    message_rows: list[dict[str, Any]] = []

    for trace in traces:
        db2 = Database()
        db2.connect()
        try:
            annotations = db2.get_annotations(trace.trace_id)
        finally:
            db2.disconnect()

        failure_categories = list(
            {a.category.value for a in annotations if a.category.value != "none"}
        )
        max_severity = _max_severity(annotations)

        trace_rows.append(
            {
                "trace_id": str(trace.trace_id),
                "framework": trace.framework,
                "task_id": trace.task_id,
                "task_category": trace.task_category,
                "task_difficulty": trace.task_difficulty,
                "task_description": trace.task_description,
                "ground_truth_type": trace.ground_truth_type.value,
                "ground_truth": json.dumps(trace.ground_truth) if trace.ground_truth else None,
                "actual_output": trace.actual_output,
                "total_api_tokens": trace.total_api_tokens,
                "total_content_tokens": trace.total_content_tokens,
                "context_overhead_ratio": trace.context_overhead_ratio,
                "total_time_seconds": trace.total_time_seconds,
                "num_agents": trace.num_agents,
                "num_messages": len(trace.messages),
                "num_content_messages": len(trace.content_messages),
                "task_success": trace.task_success,
                "task_score": trace.task_score,
                "model_used": trace.model_used,
                "run_timestamp": trace.run_timestamp.isoformat(),
                "has_failure": len(failure_categories) > 0,
                "failure_categories": json.dumps(failure_categories),
                "max_severity": max_severity,
                "num_annotations": len(annotations),
            }
        )

        for msg in trace.messages:
            message_rows.append(
                {
                    "trace_id": str(trace.trace_id),
                    "message_index": msg.message_index,
                    "source_agent": msg.source_agent,
                    "target_agent": msg.target_agent,
                    "content": msg.content,
                    "message_type": msg.message_type.value,
                    "is_content_message": msg.message_type.is_content,
                    "api_token_count": msg.api_token_count,
                    "content_token_count": msg.content_token_count,
                    "model_used": msg.model_used,
                }
            )

    traces_df = pd.DataFrame(trace_rows)
    messages_df = pd.DataFrame(message_rows)

    # ── Train / test split ─────────────────────────────────────────────────────
    n = len(traces_df)
    n_train = int(n * train_frac)
    train_df = traces_df.iloc[:n_train]
    test_df = traces_df.iloc[n_train:]

    # Messages follow their trace split
    train_ids = set(train_df["trace_id"])
    test_ids = set(test_df["trace_id"])
    train_msgs = messages_df[messages_df["trace_id"].isin(train_ids)]
    test_msgs = messages_df[messages_df["trace_id"].isin(test_ids)]

    # ── Write Parquet ──────────────────────────────────────────────────────────
    train_df.to_parquet(out_dir / "train_traces.parquet", index=False)
    test_df.to_parquet(out_dir / "test_traces.parquet", index=False)
    train_msgs.to_parquet(out_dir / "train_messages.parquet", index=False)
    test_msgs.to_parquet(out_dir / "test_messages.parquet", index=False)

    # ── Dataset card stats ────────────────────────────────────────────────────
    stats = {
        "total_traces": n,
        "train_traces": len(train_df),
        "test_traces": len(test_df),
        "total_messages": len(messages_df),
        "frameworks": sorted(traces_df["framework"].unique().tolist()),
        "task_categories": sorted(traces_df["task_category"].unique().tolist()),
        "failure_rate": round(traces_df["has_failure"].mean(), 3),
        "success_rate": round(traces_df["task_success"].dropna().mean(), 3)
        if traces_df["task_success"].notna().any()
        else None,
    }

    with open(out_dir / "dataset_stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    # ── README / dataset card ─────────────────────────────────────────────────
    _write_dataset_card(out_dir, stats)

    print(f"\nExport complete → {out_dir}")
    print(f"  Traces  : {stats['train_traces']} train / {stats['test_traces']} test")
    print(f"  Messages: {len(messages_df):,} total")
    print(f"  Failure rate: {stats['failure_rate']:.1%}")
    print(f"  Frameworks: {', '.join(stats['frameworks'])}")


def _max_severity(annotations: list) -> str:
    order = {"critical": 3, "major": 2, "minor": 1, "none": 0}
    if not annotations:
        return "none"
    return max((a.severity.value for a in annotations), key=lambda s: order.get(s, 0))


def _write_dataset_card(out_dir: Path, stats: dict) -> None:
    card = f"""---
license: apache-2.0
task_categories:
  - text-classification
  - text-generation
language:
  - en
tags:
  - multi-agent
  - llm-failures
  - benchmark
  - crewai
  - autogen
  - langgraph
  - metagpt
size_categories:
  - 1K<n<10K
---

# AgentFailDB

**The first benchmark dataset for failure modes in multi-agent LLM systems.**

> **{stats["failure_rate"]:.1%} of multi-agent runs exhibit at least one failure mode.**

## Dataset Description

AgentFailDB contains {stats["total_traces"]:,} annotated execution traces from four
multi-agent frameworks (CrewAI, AutoGen, LangGraph, MetaGPT) across five task
categories and four difficulty levels.  Each trace is annotated with one or more
of seven failure categories derived from a formal taxonomy.

## Failure Taxonomy

| Category | Description |
|----------|-------------|
| `cascading_hallucination` | False claims propagate across the agent chain |
| `delegation_loop` | Agents pass the task back and forth without progress |
| `context_degradation` | Information loses fidelity at each handoff |
| `conflicting_outputs` | Agents produce irreconcilable contradictory results |
| `role_violation` | An agent operates outside its assigned domain |
| `silent_failure` | System produces wrong output with no error signals |
| `resource_exhaustion` | Task consumes excessive tokens/time |

## Quick Start

```python
from datasets import load_dataset

ds = load_dataset("agentfaildb/agentfaildb")
train = ds["train"]

# Filter to failure traces only
failures = train.filter(lambda x: x["has_failure"])

# Failure rate by framework
import pandas as pd
df = train.to_pandas()
print(df.groupby("framework")["has_failure"].mean().sort_values(ascending=False))
```

## Files

| File | Description |
|------|-------------|
| `train_traces.parquet` | {stats["train_traces"]} training traces (one row per task run) |
| `test_traces.parquet` | {stats["test_traces"]} test traces |
| `train_messages.parquet` | Individual agent messages for training traces |
| `test_messages.parquet` | Individual agent messages for test traces |

## Citation

```bibtex
@misc{{agentfaildb2026,
  title={{AgentFailDB: A Benchmark for Failure Modes in Multi-Agent LLM Systems}},
  year={{2026}},
  url={{https://github.com/yourusername/agentfaildb}}
}}
```
"""
    with open(out_dir / "README.md", "w") as f:
        f.write(card)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

    parser = argparse.ArgumentParser(description="Export AgentFailDB to HuggingFace Parquet format")
    parser.add_argument(
        "--out-dir",
        default="data/hf_export",
        help="Output directory (default: data/hf_export)",
    )
    parser.add_argument(
        "--split",
        default="80/20",
        help="Train/test split ratio, e.g. 80/20 (default: 80/20)",
    )
    args = parser.parse_args()

    parts = args.split.split("/")
    if len(parts) != 2:
        raise SystemExit("--split must be in format TRAIN/TEST, e.g. 80/20")
    train_frac = int(parts[0]) / (int(parts[0]) + int(parts[1]))

    _export(Path(args.out_dir), train_frac=train_frac)
