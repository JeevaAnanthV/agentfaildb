"""
Day-5 smoke test for AgentFailDB.

Runs one task (cr_easy_001) on LangGraph, verifies the trace is stored in
PostgreSQL, confirms the detector annotation ran, and prints a summary.

Usage:
    python3 smoke_test.py
    python3 smoke_test.py --framework langgraph --task cr_easy_001
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("smoke_test")


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentFailDB smoke test")
    parser.add_argument("--framework", default="langgraph", help="Framework to test")
    parser.add_argument("--task", default="cr_easy_001", help="Task ID to run")
    parser.add_argument("--annotate", action="store_true", help="Run LLM annotation pass")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  AgentFailDB Smoke Test")
    print(f"  Framework : {args.framework}")
    print(f"  Task      : {args.task}")
    print(f"{'='*60}\n")

    # ── Step 1: Infrastructure health ────────────────────────────────────────
    print("[1/5] Checking infrastructure...")

    from agentfaildb.config import settings
    print(f"      Ollama  : {settings.ollama_base_url}")
    print(f"      Postgres: {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")
    print(f"      Redis   : {settings.redis_host}:{settings.redis_port}")
    print(f"      Model   : {settings.task_model}")

    # Test Ollama reachability
    import httpx
    try:
        r = httpx.get(f"http://{settings.ollama_base_url.split('//')[1].rsplit('/v1', 1)[0]}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        print(f"      Ollama models: {models}")
        if settings.task_model not in models:
            print(f"  WARNING: model '{settings.task_model}' not found in Ollama. Run: ollama pull {settings.task_model}")
    except Exception as e:
        print(f"  ERROR: Cannot reach Ollama at configured URL: {e}")
        print("  Check OLLAMA_BASE_URL in .env")
        return 1

    # ── Step 2: Database connection ───────────────────────────────────────────
    print("\n[2/5] Connecting to PostgreSQL + Redis...")
    from agentfaildb.harness.db import Database, RedisClient

    db = Database()
    try:
        db.connect()
        print("      PostgreSQL: connected")
    except Exception as e:
        print(f"  ERROR: PostgreSQL connection failed: {e}")
        return 1

    redis_client = None
    try:
        redis_client = RedisClient()
        redis_client.connect()
        print("      Redis: connected")
    except Exception as e:
        print(f"  WARNING: Redis unavailable (non-fatal): {e}")

    # ── Step 3: Load task ─────────────────────────────────────────────────────
    print(f"\n[3/5] Loading task '{args.task}'...")
    from agentfaildb.tasks import ALL_TASKS
    task = next((t for t in ALL_TASKS if t.task_id == args.task), None)
    if task is None:
        print(f"  ERROR: Task '{args.task}' not found.")
        print(f"  Available tasks: {[t.task_id for t in ALL_TASKS[:10]]}")
        return 1

    print(f"      Task: {task.task_id} ({task.difficulty})")
    print(f"      Category: {task.task_category}")
    print(f"      Agents: {list(task.canonical_roles.keys())}")
    print(f"      Description: {task.description[:80]}...")

    # ── Step 4: Execute trace ─────────────────────────────────────────────────
    print(f"\n[4/5] Executing trace on '{args.framework}'...")
    print("      (This may take 30-120s — LLM calls via Ollama)\n")

    from agentfaildb.detector import FailureDetector
    from agentfaildb.evaluator import GroundTruthEvaluator
    from agentfaildb.harness.orchestrator import Orchestrator

    detector = FailureDetector(redis_client=redis_client)
    evaluator = GroundTruthEvaluator()
    orchestrator = Orchestrator(db=db, detector=detector, evaluator=evaluator)

    t0 = time.time()
    try:
        trace = orchestrator.run_task_on_framework(
            task=task,
            framework=args.framework,
            annotate=args.annotate,
        )
    except Exception as e:
        logger.exception("run_task_on_framework failed")
        print(f"\n  ERROR: Task execution failed: {e}")
        return 1

    elapsed = time.time() - t0

    # ── Step 5: Verify results ────────────────────────────────────────────────
    print(f"\n[5/5] Verifying results...")

    ok = True

    # Check the trace object
    print(f"      trace_id       : {trace.trace_id}")
    print(f"      framework      : {trace.framework}")
    print(f"      messages       : {len(trace.messages)}")
    print(f"      total_time     : {trace.total_time_seconds:.1f}s  (wall: {elapsed:.1f}s)")
    print(f"      task_success   : {trace.task_success}")
    print(f"      task_score     : {trace.task_score}")
    print(f"      annotations    : {len(trace.annotations)}")

    if not trace.messages:
        print("  WARNING: Zero messages recorded — callback hooks may not be wiring correctly")
        ok = False
    else:
        print(f"      First message  : [{trace.messages[0].source_agent} → {trace.messages[0].target_agent}]")
        print(f"      Last message   : [{trace.messages[-1].source_agent} → {trace.messages[-1].target_agent}]")

    if trace.actual_output:
        print(f"      output preview : {trace.actual_output[:120]}...")
    else:
        print("  WARNING: Empty actual_output — run may have timed out or errored")
        ok = False

    if trace.run_config.get("run_error") or trace.run_config.get("setup_error"):
        print(f"  ERROR in run_config: {trace.run_config}")
        ok = False

    # Check DB — confirm the row exists
    try:
        import psycopg2
        import psycopg2.extras
        verify_conn = psycopg2.connect(
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_db} user={settings.postgres_user} "
            f"password={settings.postgres_password}"
        )
        with verify_conn.cursor() as cur:
            cur.execute(
                "SELECT trace_id, framework, num_agents, task_success, task_score "
                "FROM traces WHERE trace_id = %s",
                (str(trace.trace_id),),
            )
            row = cur.fetchone()
            if row:
                print(f"      DB row found   : trace_id={row[0]}, framework={row[1]}, "
                      f"num_agents={row[2]}, success={row[3]}, score={row[4]}")
            else:
                print("  WARNING: Trace not found in DB — insert may have failed")
                ok = False
            cur.execute("SELECT COUNT(*) FROM messages WHERE trace_id = %s", (str(trace.trace_id),))
            msg_count = cur.fetchone()[0]
            print(f"      DB messages    : {msg_count} rows in messages table")
            cur.execute("SELECT COUNT(*) FROM traces")
            total = cur.fetchone()[0]
            print(f"      DB total traces: {total}")
        verify_conn.close()
    except Exception as e:
        print(f"  WARNING: DB verification query failed: {e}")
        ok = False

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    if ok:
        print("  SMOKE TEST PASSED")
    else:
        print("  SMOKE TEST COMPLETED WITH WARNINGS — review above")
    print(f"  Total time: {elapsed:.1f}s")
    print(f"{'='*60}\n")

    db.disconnect()
    if redis_client is not None:
        redis_client.disconnect()

    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
