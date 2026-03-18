"""
Day-5 validation batch for AgentFailDB.

Runs all easy tasks on LangGraph (skipping already-completed cr_easy_001),
then summarises results — success rate, annotation counts, failure categories.

Usage:
    python3 run_validation_batch.py
    python3 run_validation_batch.py --difficulty easy --skip-existing
    python3 run_validation_batch.py --difficulty easy medium --frameworks langgraph
"""

from __future__ import annotations

import argparse
import logging
import time
from collections import Counter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("validation_batch")


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentFailDB validation batch runner")
    parser.add_argument(
        "--difficulty",
        nargs="+",
        default=["easy"],
        choices=["easy", "medium", "hard", "adversarial"],
        help="Difficulty levels to run (default: easy)",
    )
    parser.add_argument(
        "--frameworks",
        nargs="+",
        default=["langgraph"],
        help="Frameworks to run (default: langgraph)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip task+framework combos already in DB (default: True)",
    )
    parser.add_argument(
        "--no-skip",
        dest="skip_existing",
        action="store_false",
        help="Run all tasks regardless of existing DB records",
    )
    args = parser.parse_args()

    print(f"\n{'='*65}")
    print("  AgentFailDB Validation Batch")
    print(f"  Difficulties : {args.difficulty}")
    print(f"  Frameworks   : {args.frameworks}")
    print(f"  Skip existing: {args.skip_existing}")
    print(f"{'='*65}\n")

    # ── Infrastructure ─────────────────────────────────────────────────────
    from agentfaildb.config import settings
    from agentfaildb.detector import FailureDetector
    from agentfaildb.evaluator import GroundTruthEvaluator
    from agentfaildb.harness.db import Database, RedisClient
    from agentfaildb.harness.orchestrator import Orchestrator
    from agentfaildb.tasks import ALL_TASKS, get_tasks_by_difficulty

    db = Database()
    db.connect()

    redis_client = None
    try:
        redis_client = RedisClient()
        redis_client.connect()
        print("[infra] Redis connected")
    except Exception as e:
        print(f"[infra] Redis unavailable (non-fatal): {e}")

    detector = FailureDetector(redis_client=redis_client)
    evaluator = GroundTruthEvaluator()
    orchestrator = Orchestrator(db=db, detector=detector, evaluator=evaluator)

    # ── Build task list ───────────────────────────────────────────────────
    task_list: list = []
    for diff in args.difficulty:
        task_list.extend(get_tasks_by_difficulty(diff))

    # Optionally skip tasks already in DB
    if args.skip_existing:
        import psycopg2
        import psycopg2.extras
        psycopg2.extras.register_uuid()
        skip_conn = psycopg2.connect(settings.postgres_dsn)
        with skip_conn.cursor() as cur:
            cur.execute("SELECT task_id, framework FROM traces")
            existing = {(row[0], row[1]) for row in cur.fetchall()}
        skip_conn.close()
        print(f"[batch] Found {len(existing)} existing (task_id, framework) pairs in DB — will skip.")
        task_fw_pairs = [
            (task, fw)
            for task in task_list
            for fw in args.frameworks
            if (task.task_id, fw) not in existing
        ]
    else:
        task_fw_pairs = [(task, fw) for task in task_list for fw in args.frameworks]

    total = len(task_fw_pairs)
    if total == 0:
        print("[batch] Nothing to run — all tasks already in DB.")
        db.disconnect()
        return 0

    print(f"[batch] Running {total} tasks × framework combos...\n")

    # ── Run loop ──────────────────────────────────────────────────────────
    results = []
    failed = 0
    t0 = time.time()

    for idx, (task, framework) in enumerate(task_fw_pairs, 1):
        elapsed_total = time.time() - t0
        eta = ""
        if idx > 1:
            rate = elapsed_total / (idx - 1)
            remaining = (total - idx + 1) * rate
            eta = f" | ETA ~{remaining/60:.1f}min"

        print(
            f"[{idx:02d}/{total}] {task.task_id} ({task.difficulty}) on {framework}{eta}",
            flush=True,
        )

        run_t0 = time.time()
        try:
            trace = orchestrator.run_task_on_framework(
                task=task,
                framework=framework,
                annotate=False,
            )
            run_elapsed = time.time() - run_t0
            status = "PASS" if trace.task_success else "FAIL"
            ann_count = len(trace.annotations)
            ann_cats = [a.category.value for a in trace.annotations]
            print(
                f"         -> {status} | score={trace.task_score:.2f} | "
                f"msgs={len(trace.messages)} | annotations={ann_count} {ann_cats} | "
                f"time={run_elapsed:.0f}s"
            )
            results.append(trace)
        except Exception as exc:
            run_elapsed = time.time() - run_t0
            logger.error("Task %s on %s failed: %s", task.task_id, framework, exc)
            print(f"         -> ERROR: {exc} (elapsed={run_elapsed:.0f}s)")
            failed += 1

    # ── Summary ───────────────────────────────────────────────────────────
    total_elapsed = time.time() - t0
    successes = sum(1 for t in results if t.task_success)
    total_ran = len(results)
    all_annotations = [a for t in results for a in t.annotations]
    annotation_counter = Counter(a.category.value for a in all_annotations)

    print(f"\n{'='*65}")
    print("  BATCH SUMMARY")
    print(f"{'='*65}")
    print(f"  Ran         : {total_ran} tasks ({failed} errors)")
    print(f"  Succeeded   : {successes}/{total_ran} ({100*successes/max(total_ran,1):.0f}%)")
    print(f"  Total time  : {total_elapsed/60:.1f} min")
    print(f"  Avg per run : {total_elapsed/max(total_ran,1):.0f}s")
    print()
    print(f"  Failure annotations detected: {len(all_annotations)}")
    for cat, count in annotation_counter.most_common():
        print(f"    {cat:<30} : {count}")
    print()

    # Check total DB traces
    import psycopg2
    import psycopg2.extras
    psycopg2.extras.register_uuid()
    check_conn = psycopg2.connect(settings.postgres_dsn)
    with check_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM traces")
        db_total = cur.fetchone()[0]
        cur.execute(
            "SELECT framework, COUNT(*), SUM(CASE WHEN task_success THEN 1 ELSE 0 END) "
            "FROM traces GROUP BY framework"
        )
        fw_rows = cur.fetchall()
    check_conn.close()

    print(f"  Total traces in DB: {db_total}")
    for fw, total_count, success_count in fw_rows:
        print(f"    {fw:<15}: {total_count} traces, {success_count} successes")
    print(f"{'='*65}\n")

    db.disconnect()
    if redis_client:
        redis_client.disconnect()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
