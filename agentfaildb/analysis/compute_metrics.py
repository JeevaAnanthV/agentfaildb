"""
Metrics computation script for AgentFailDB.

Connects to PostgreSQL, computes all metrics, and saves results to
analysis_results/metrics.json.

Usage:
  python -m agentfaildb.analysis.compute_metrics
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    from agentfaildb.harness.db import Database  # noqa: PLC0415
    from agentfaildb.metrics import (  # noqa: PLC0415
        compute_context_overhead_stats,
        compute_failure_rates,
        compute_resource_exhaustion_baselines,
    )

    logger.info("Connecting to PostgreSQL...")
    db = Database()
    try:
        db.connect()
    except Exception as exc:
        logger.error("Failed to connect to PostgreSQL: %s", exc)
        return 1

    logger.info("Computing failure rates...")
    failure_rates = compute_failure_rates(db)

    logger.info("Computing per-framework failure rates...")
    per_framework: dict[str, dict] = {}
    for fw in ["crewai", "autogen", "langgraph", "metagpt"]:
        per_framework[fw] = compute_failure_rates(db, framework=fw)

    logger.info("Computing resource exhaustion baselines...")
    baselines = compute_resource_exhaustion_baselines(db)

    logger.info("Computing context overhead stats...")
    overhead_stats = compute_context_overhead_stats(db)

    db.disconnect()

    # Assemble results
    results = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "failure_rates": failure_rates,
        "per_framework_failure_rates": per_framework,
        "resource_exhaustion_baselines": baselines,
        "context_overhead_stats": overhead_stats,
    }

    # Save to file
    output_dir = Path("analysis_results")
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / "metrics.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    logger.info("Metrics saved to %s", output_path.resolve())
    logger.info("Total traces analysed: %d", failure_rates.get("total_traces", 0))

    # Print summary to stdout
    print("\n=== AgentFailDB Metrics Summary ===")
    print(f"Total traces: {failure_rates.get('total_traces', 0)}")
    print("\nFailure rates by framework:")
    for fw, data in failure_rates.get("by_framework", {}).items():
        print(f"  {fw}: {data.get('failure_rate', 0):.1%} failure rate")
    print("\nTop failure categories:")
    by_cat = failure_rates.get("by_category", {})
    sorted_cats = sorted(by_cat.items(), key=lambda x: x[1].get("rate", 0), reverse=True)
    for cat, data in sorted_cats[:5]:
        print(f"  {cat}: {data.get('rate', 0):.1%}")
    print(f"\nResults saved to: {output_path.resolve()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
