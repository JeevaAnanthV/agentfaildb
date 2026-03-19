"""
Orchestrator for AgentFailDB benchmark runs.

Manages the full pipeline: runner selection → task execution → evaluation
→ failure detection → database storage.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from datetime import datetime
from typing import Any

from agentfaildb.detector import FailureDetector
from agentfaildb.evaluator import GroundTruthEvaluator
from agentfaildb.harness.db import Database
from agentfaildb.harness.trace_collector import get_collector
from agentfaildb.runners import get_runner_class
from agentfaildb.tasks import ALL_TASKS, get_tasks_by_category, get_tasks_by_difficulty
from agentfaildb.tasks.base_task import BaseTask
from agentfaildb.trace import TaskTrace

logger = logging.getLogger(__name__)

_SUPPORTED_FRAMEWORKS = ["crewai", "autogen", "langgraph"]

# Redis key prefix for checkpoint tracking
_CHECKPOINT_KEY = "agentfaildb:checkpoint"

# Graceful shutdown flag — set by SIGTERM/SIGINT handler
_shutdown_requested = False


def _request_shutdown(signum: int, _frame: Any) -> None:
    """Signal handler: request graceful stop after the current task finishes."""
    global _shutdown_requested  # noqa: PLW0603
    _shutdown_requested = True
    logger.info("Shutdown requested (signal %s). Will stop after current task.", signum)


# Retry configuration for transient Ollama/infra failures
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 15  # seconds; doubles each attempt (15 → 30 → 60)


def get_completed_from_db(db: Database) -> set[str]:
    """
    Query PostgreSQL for all (task_id, framework) pairs that have already been
    completed (task_success IS NOT NULL).  Returns a set of 'task_id::framework'
    strings — the same format used by the Redis checkpoint.

    This is the authoritative source of truth on restart: even if Redis was
    flushed or the machine was rebooted, we never re-run work already in the DB.
    """
    sql = "SELECT DISTINCT task_id, framework FROM traces WHERE task_success IS NOT NULL"
    completed: set[str] = set()
    try:
        if db._conn is None or db._conn.closed:
            return completed
        with db._cursor() as cur:
            cur.execute(sql)
            for row in cur.fetchall():
                completed.add(f"{row['task_id']}::{row['framework']}")
        logger.info("DB checkpoint: %d completed (task, framework) pairs loaded.", len(completed))
    except Exception as exc:
        logger.warning("Could not load completed pairs from DB: %s", exc)
    return completed


class Orchestrator:
    """
    High-level coordinator that runs tasks across frameworks and stores results.
    """

    def __init__(
        self,
        db: Database,
        detector: FailureDetector,
        evaluator: GroundTruthEvaluator,
        annotator: Any = None,
    ) -> None:
        self.db = db
        self.detector = detector
        self.evaluator = evaluator
        self.annotator = annotator

    def run_task_on_framework(
        self,
        task: BaseTask,
        framework: str,
        annotate: bool = False,
    ) -> TaskTrace:
        """
        Instantiate the correct runner, execute the task, evaluate, detect, and store.

        Returns the completed TaskTrace with annotations attached.
        """
        logger.info("Running task '%s' on framework '%s'", task.task_id, framework)

        collector = get_collector()
        RunnerClass = get_runner_class(framework)
        runner = RunnerClass(task=task, collector=collector)

        # Execute with exponential-backoff retry for transient infra failures.
        # Connection errors, timeouts talking to Ollama, and similar transient
        # issues are retried up to _MAX_RETRIES times.  Task-level timeouts
        # (handled inside BaseRunner.execute) are NOT retried here — the runner
        # itself captures them and returns a partial trace.
        trace: TaskTrace | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                trace = runner.execute()
                break
            except Exception as exc:
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "run attempt %d/%d failed for task=%s framework=%s: %s — retrying in %ds",
                        attempt,
                        _MAX_RETRIES,
                        task.task_id,
                        framework,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    # Fresh runner for each retry (collector state was flushed)
                    collector = get_collector()
                    runner = RunnerClass(task=task, collector=collector)
                else:
                    logger.error(
                        "All %d attempts failed for task=%s framework=%s: %s",
                        _MAX_RETRIES,
                        task.task_id,
                        framework,
                        exc,
                    )
                    raise

        assert trace is not None  # guaranteed: loop raises on last failure

        # Evaluate against ground truth
        try:
            task_success, task_score, method = self.evaluator.evaluate(trace)
            trace = trace.model_copy(
                update={
                    "task_success": task_success,
                    "task_score": task_score,
                    "task_success_method": method,
                }
            )
        except Exception as eval_exc:
            logger.warning("Evaluation failed for trace %s: %s", trace.trace_id, eval_exc)

        # Detect failures
        try:
            annotations = self.detector.analyze(trace)
            trace = trace.model_copy(update={"annotations": annotations})
        except Exception as det_exc:
            logger.warning("Detection failed for trace %s: %s", trace.trace_id, det_exc)

        # LLM annotation (optional)
        if annotate and self.annotator is not None:
            try:
                llm_annotations = self.annotator.annotate(trace)
                combined = list(trace.annotations) + llm_annotations
                trace = trace.model_copy(update={"annotations": combined})
            except Exception as ann_exc:
                logger.warning("LLM annotation failed for trace %s: %s", trace.trace_id, ann_exc)

        # Store to database
        try:
            self.db.insert_trace(trace)
            if trace.messages:
                self.db.insert_messages(trace.trace_id, trace.messages)
            for annotation in trace.annotations:
                if annotation.trace_id is None:
                    object.__setattr__(annotation, "trace_id", trace.trace_id)
                self.db.insert_annotation(annotation)
        except Exception as db_exc:
            logger.error("DB storage failed for trace %s: %s", trace.trace_id, db_exc)

        return trace

    def run_all(
        self,
        tasks: list[BaseTask] | None = None,
        frameworks: list[str] | None = None,
        annotate: bool = False,
    ) -> list[TaskTrace]:
        """
        Run all tasks × all frameworks.

        tasks and frameworks are optional filter lists.
        Returns all completed TaskTraces.
        """
        task_list = tasks if tasks is not None else ALL_TASKS
        framework_list = frameworks if frameworks is not None else _SUPPORTED_FRAMEWORKS

        results: list[TaskTrace] = []
        total = len(task_list) * len(framework_list)
        done = 0

        for task in task_list:
            for framework in framework_list:
                done += 1
                logger.info(
                    "Progress: %d/%d — task '%s' on '%s'",
                    done,
                    total,
                    task.task_id,
                    framework,
                )
                try:
                    trace = self.run_task_on_framework(task, framework, annotate=annotate)
                    results.append(trace)
                except Exception as exc:
                    logger.error(
                        "run_task_on_framework failed for task=%s framework=%s: %s",
                        task.task_id,
                        framework,
                        exc,
                    )

        return results

    def run_batch(
        self,
        task_category: str,
        difficulty: str,
        framework: str,
        n: int = 10,
        annotate: bool = False,
    ) -> list[TaskTrace]:
        """
        Run up to n tasks from a given category + difficulty on one framework.

        Returns completed TaskTraces.
        """
        category_tasks = get_tasks_by_category(task_category)
        difficulty_tasks = [t for t in category_tasks if t.difficulty == difficulty]

        if not difficulty_tasks:
            logger.warning(
                "No tasks found for category='%s' difficulty='%s'",
                task_category,
                difficulty,
            )
            return []

        tasks_to_run = difficulty_tasks[:n]
        results: list[TaskTrace] = []

        for task in tasks_to_run:
            try:
                trace = self.run_task_on_framework(task, framework, annotate=annotate)
                results.append(trace)
            except Exception as exc:
                logger.error(
                    "Batch run failed for task=%s: %s",
                    task.task_id,
                    exc,
                )

        return results

    def run_all_with_checkpoint(
        self,
        tasks: list[BaseTask] | None = None,
        frameworks: list[str] | None = None,
        annotate: bool = False,
        redis_client: Any = None,
        deadline: datetime | None = None,
    ) -> list[TaskTrace]:
        """
        Run all tasks × all frameworks with Redis-backed checkpoint/resume.

        If a previous run was interrupted, completed (task_id, framework) pairs
        are skipped automatically.  Progress is printed to stdout.
        """
        task_list = tasks if tasks is not None else ALL_TASKS
        framework_list = frameworks if frameworks is not None else _SUPPORTED_FRAMEWORKS

        # --- Authoritative deduplication: seed from PostgreSQL first -----------
        # Even if Redis was cleared (reboot, manual flush), the DB is the truth.
        completed_pairs: set[str] = get_completed_from_db(self.db)

        # Merge Redis checkpoint on top (may contain pairs in-flight that were
        # not yet committed to DB if the previous run crashed mid-insert).
        if redis_client is not None:
            try:
                raw = redis_client._client.get(_CHECKPOINT_KEY)
                if raw:
                    redis_pairs = set(json.loads(raw))
                    before = len(completed_pairs)
                    completed_pairs |= redis_pairs
                    logger.info(
                        "Redis checkpoint merged: %d pairs (DB=%d, Redis=%d, union=%d).",
                        len(redis_pairs),
                        before,
                        len(redis_pairs),
                        len(completed_pairs),
                    )
            except Exception as ck_exc:
                logger.warning("Could not load Redis checkpoint: %s", ck_exc)

        results: list[TaskTrace] = []
        total = len(task_list) * len(framework_list)
        done = 0
        skipped = 0
        failed = 0
        t0 = time.time()

        _stop_reason: str | None = None

        for task in task_list:
            if _stop_reason:
                break
            for framework in framework_list:
                done += 1
                pair_key = f"{task.task_id}::{framework}"

                if pair_key in completed_pairs:
                    skipped += 1
                    continue

                # Check for graceful stop conditions before starting next task
                if _shutdown_requested:
                    _stop_reason = "Shutdown requested (signal)"
                    break
                if deadline is not None and datetime.now() >= deadline:
                    _stop_reason = f"Deadline {deadline:%H:%M} reached"
                    break

                elapsed = time.time() - t0
                remaining = total - done
                eta_str = ""
                if done - skipped > 0:
                    rate = elapsed / (done - skipped)
                    eta_str = f" | ETA {rate * remaining:.0f}s"

                print(
                    f"[{done}/{total}] {task.task_id} on {framework}"
                    f" (skip={skipped} fail={failed}{eta_str})",
                    flush=True,
                )

                try:
                    trace = self.run_task_on_framework(task, framework, annotate=annotate)
                    results.append(trace)

                    # Mark complete in checkpoint
                    if redis_client is not None:
                        try:
                            completed_pairs.add(pair_key)
                            redis_client._client.set(
                                _CHECKPOINT_KEY,
                                json.dumps(list(completed_pairs)),
                            )
                        except Exception:
                            pass  # checkpoint failure is non-fatal

                except Exception as exc:
                    failed += 1
                    logger.error(
                        "run_task_on_framework failed task=%s framework=%s: %s",
                        task.task_id,
                        framework,
                        exc,
                    )

        elapsed_total = time.time() - t0
        reason_str = f" ({_stop_reason})" if _stop_reason else ""
        print(
            f"\nRun complete{reason_str}: {len(results)} succeeded, {failed} failed, "
            f"{skipped} skipped. Total time: {elapsed_total:.0f}s",
            flush=True,
        )

        # Clear checkpoint on full completion
        if redis_client is not None and failed == 0:
            try:
                redis_client._client.delete(_CHECKPOINT_KEY)
            except Exception:
                pass

        return results


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="AgentFailDB benchmark runner")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print progress stats from DB and exit — does not run any tasks.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Max number of tasks to run (across all categories). Default: all.",
    )
    parser.add_argument(
        "--frameworks",
        nargs="+",
        default=None,
        choices=_SUPPORTED_FRAMEWORKS,
        help="Frameworks to run. Default: all three.",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="Filter to one task category.",
    )
    parser.add_argument(
        "--difficulty",
        default=None,
        choices=["easy", "medium", "hard", "adversarial"],
        help="Filter to one difficulty level.",
    )
    parser.add_argument(
        "--until",
        type=str,
        default=None,
        metavar="HH:MM",
        help="Stop after the current task once this wall-clock time is reached (e.g. 08:00).",
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="Run LLM annotation after each trace.",
    )
    parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Disable checkpoint/resume (start fresh even if checkpoint exists).",
    )
    args = parser.parse_args()

    # Install signal handlers for graceful shutdown (systemctl stop, Ctrl-C)
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    # Parse --until into a deadline datetime
    deadline: datetime | None = None
    if args.until:
        hour, minute = (int(x) for x in args.until.split(":"))
        deadline = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if deadline <= datetime.now():
            # Time already passed today — means tomorrow
            from datetime import timedelta

            deadline += timedelta(days=1)
        print(f"Deadline set: will stop after {deadline:%Y-%m-%d %H:%M}", flush=True)

    # Connect to infrastructure
    db = Database()
    db.connect()

    # ── Status-only mode ──────────────────────────────────────────────────────
    if args.status:
        try:
            total_tasks = len(ALL_TASKS)
            total_frameworks = len(_SUPPORTED_FRAMEWORKS)
            total_runs = total_tasks * total_frameworks

            status_sql = """
                SELECT
                    COUNT(*) AS total_traces,
                    COUNT(*) FILTER (WHERE task_success IS NOT NULL) AS completed,
                    COUNT(*) FILTER (WHERE task_success = TRUE) AS succeeded,
                    COUNT(*) FILTER (WHERE task_success = FALSE) AS failed
                FROM traces
            """
            breakdown_sql = """
                SELECT framework,
                       COUNT(*) FILTER (WHERE task_success IS NOT NULL) AS done
                FROM traces
                WHERE task_success IS NOT NULL
                GROUP BY framework
                ORDER BY framework
            """
            with db._cursor() as cur:
                cur.execute(status_sql)
                row = dict(cur.fetchone())
                cur.execute(breakdown_sql)
                breakdown = [dict(r) for r in cur.fetchall()]

            completed_pairs = get_completed_from_db(db)
            remaining = total_runs - len(completed_pairs)
            pct = 100.0 * len(completed_pairs) / total_runs if total_runs else 0

            print(f"\n{'=' * 55}")
            print("  AgentFailDB Benchmark Status")
            print(f"{'=' * 55}")
            print(f"  Total task definitions : {total_tasks}")
            print(f"  Frameworks             : {', '.join(_SUPPORTED_FRAMEWORKS)}")
            print(f"  Total planned runs     : {total_runs}")
            print(f"  Completed (distinct)   : {len(completed_pairs)}  ({pct:.1f}%)")
            print(f"  Remaining              : {remaining}")
            print(f"  DB rows (all)          : {row['total_traces']}")
            print(f"  Succeeded              : {row['succeeded']}")
            print(f"  Failed                 : {row['failed']}")
            print("\n  Per-framework progress:")
            for b in breakdown:
                print(f"    {b['framework']:<12} {b['done']} done")
            print(f"{'=' * 55}\n")
        except Exception as status_exc:
            print(f"Status query failed: {status_exc}")
        finally:
            db.disconnect()
        sys.exit(0)

    # ── Normal run ────────────────────────────────────────────────────────────
    redis_client = None
    if not args.no_checkpoint:
        try:
            from agentfaildb.harness.db import RedisClient  # noqa: PLC0415

            redis_client = RedisClient()
            redis_client.connect()
            print("Redis checkpoint enabled.")
        except Exception as r_exc:
            print(f"Redis unavailable — checkpoint disabled: {r_exc}")

    detector = FailureDetector(redis_client=redis_client)
    evaluator = GroundTruthEvaluator()
    orchestrator = Orchestrator(db=db, detector=detector, evaluator=evaluator)

    # Build task list
    if args.category:
        task_list = get_tasks_by_category(args.category)
    elif args.difficulty:
        task_list = get_tasks_by_difficulty(args.difficulty)
    else:
        task_list = list(ALL_TASKS)

    if args.count is not None:
        task_list = task_list[: args.count]

    framework_list = args.frameworks or _SUPPORTED_FRAMEWORKS
    total_planned = len(task_list) * len(framework_list)

    print(
        f"Running {len(task_list)} tasks x {len(framework_list)} frameworks "
        f"= {total_planned} total runs.",
        flush=True,
    )

    orchestrator.run_all_with_checkpoint(
        tasks=task_list,
        frameworks=args.frameworks,
        annotate=args.annotate,
        redis_client=redis_client,
        deadline=deadline,
    )

    db.disconnect()
    if redis_client is not None:
        redis_client.disconnect()
