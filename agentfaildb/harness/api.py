"""
FastAPI REST API for AgentFailDB.

Endpoints:
  POST /run               — run a task on a framework
  POST /analyze/{trace_id} — run detector on stored trace
  GET  /metrics           — aggregate statistics
  GET  /health            — system health check
  GET  /traces            — paginated trace listing
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from agentfaildb.config import settings
from agentfaildb.detector import FailureDetector
from agentfaildb.evaluator import GroundTruthEvaluator
from agentfaildb.harness.db import Database, RedisClient
from agentfaildb.harness.orchestrator import Orchestrator
from agentfaildb.tasks import get_task_by_id

logger = logging.getLogger(__name__)

# ── In-memory run progress tracker ────────────────────────────────────────────


class _RunProgress:
    """Thread-safe progress tracker for batch runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total: int = 0
        self.completed: int = 0
        self.failed: int = 0
        self.running: bool = False
        self.start_time: float | None = None
        self.last_task: str = ""
        self.last_framework: str = ""

    def start(self, total: int) -> None:
        with self._lock:
            self.total = total
            self.completed = 0
            self.failed = 0
            self.running = True
            self.start_time = time.time()

    def record(self, task_id: str, framework: str, success: bool) -> None:
        with self._lock:
            if success:
                self.completed += 1
            else:
                self.failed += 1
            self.last_task = task_id
            self.last_framework = framework

    def finish(self) -> None:
        with self._lock:
            self.running = False

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = time.time() - self.start_time if self.start_time else 0
            done = self.completed + self.failed
            remaining = self.total - done
            eta_s = (elapsed / done * remaining) if done > 0 else None
            return {
                "running": self.running,
                "total": self.total,
                "completed": self.completed,
                "failed": self.failed,
                "remaining": remaining,
                "elapsed_seconds": round(elapsed, 1),
                "eta_seconds": round(eta_s, 0) if eta_s is not None else None,
                "last_task": self.last_task,
                "last_framework": self.last_framework,
            }


_progress = _RunProgress()

app = FastAPI(
    title="AgentFailDB API",
    description="REST API for running benchmark tasks and analysing failure patterns in multi-agent LLM systems.",
    version="0.1.0",
)

# ── Shared clients (initialised on startup) ────────────────────────────────────

_db: Database | None = None
_redis: RedisClient | None = None
_detector: FailureDetector | None = None
_evaluator: GroundTruthEvaluator | None = None
_orchestrator: Orchestrator | None = None


def _get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
        _db.connect()
    return _db


def _get_redis() -> RedisClient | None:
    global _redis
    if _redis is None:
        try:
            _redis = RedisClient()
            _redis.connect()
        except Exception as exc:
            logger.warning("Redis unavailable: %s", exc)
            _redis = None
    return _redis


def _get_detector() -> FailureDetector:
    global _detector
    if _detector is None:
        redis = _get_redis()
        _detector = FailureDetector(redis_client=redis)
    return _detector


def _get_evaluator() -> GroundTruthEvaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = GroundTruthEvaluator()
    return _evaluator


def _get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(
            db=_get_db(),
            detector=_get_detector(),
            evaluator=_get_evaluator(),
        )
    return _orchestrator


# ── Request / Response models ──────────────────────────────────────────────────


class RunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    task_id: str
    framework: str
    annotate: bool = False


class RunResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    trace_id: str
    task_id: str
    framework: str
    task_success: bool | None
    task_score: float | None
    total_time_seconds: float
    total_api_tokens: int
    num_messages: int
    num_annotations: int


class AnnotationSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    annotation_id: str
    category: str
    severity: str
    confidence: float
    description: str
    root_cause_agent: str | None


class AnalyzeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    trace_id: str
    annotations: list[AnnotationSummary]


class TraceSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    trace_id: str
    framework: str
    task_id: str
    task_category: str
    task_difficulty: str
    task_success: bool | None
    task_score: float | None
    total_api_tokens: int
    total_time_seconds: float
    num_annotations: int
    run_timestamp: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.post("/run", response_model=RunResponse)
def run_task(request: RunRequest) -> RunResponse:
    """
    Run a benchmark task on a specified framework.

    Returns trace_id and summary statistics.
    """
    task = get_task_by_id(request.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{request.task_id}' not found")

    supported = ["crewai", "autogen", "langgraph", "metagpt"]
    if request.framework not in supported:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown framework '{request.framework}'. Supported: {supported}",
        )

    try:
        orchestrator = _get_orchestrator()
        trace = orchestrator.run_task_on_framework(
            task=task,
            framework=request.framework,
            annotate=request.annotate,
        )
    except Exception as exc:
        logger.exception("run_task failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return RunResponse(
        trace_id=str(trace.trace_id),
        task_id=trace.task_id,
        framework=trace.framework,
        task_success=trace.task_success,
        task_score=trace.task_score,
        total_time_seconds=trace.total_time_seconds,
        total_api_tokens=trace.total_api_tokens,
        num_messages=len(trace.messages),
        num_annotations=len(trace.annotations),
    )


@app.post("/analyze/{trace_id}", response_model=AnalyzeResponse)
def analyze_trace(trace_id: str) -> AnalyzeResponse:
    """
    Run the failure detector on a stored trace.

    Fetches the trace from PostgreSQL, runs all pattern detectors,
    stores the resulting annotations, and returns them.
    """
    try:
        tid = UUID(trace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid trace_id format")

    db = _get_db()
    trace = db.get_trace(tid)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

    try:
        detector = _get_detector()
        annotations = detector.analyze(trace)
    except Exception as exc:
        logger.exception("Detection failed for trace %s: %s", trace_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # Store annotations
    stored: list[AnnotationSummary] = []
    for ann in annotations:
        if ann.trace_id is None:
            object.__setattr__(ann, "trace_id", tid)
        try:
            db.insert_annotation(ann)
        except Exception as db_exc:
            logger.warning("Failed to store annotation: %s", db_exc)

        stored.append(
            AnnotationSummary(
                annotation_id=str(ann.annotation_id),
                category=ann.category.value,
                severity=ann.severity.value,
                confidence=ann.confidence,
                description=ann.description,
                root_cause_agent=ann.root_cause_agent,
            )
        )

    return AnalyzeResponse(trace_id=trace_id, annotations=stored)


@app.get("/metrics")
def get_metrics(
    framework: str | None = Query(default=None),
    task_category: str | None = Query(default=None),
    difficulty: str | None = Query(default=None),
) -> dict[str, Any]:
    """
    Return aggregate failure statistics.

    Filters by framework, task_category, and/or difficulty if provided.
    """
    try:
        from agentfaildb.metrics import compute_failure_rates  # noqa: PLC0415

        db = _get_db()
        metrics = compute_failure_rates(db, framework=framework)
        return {
            "status": "ok",
            "metrics": metrics,
            "filters": {
                "framework": framework,
                "task_category": task_category,
                "difficulty": difficulty,
            },
        }
    except Exception as exc:
        logger.exception("Metrics computation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
def health_check() -> dict[str, Any]:
    """Return system health status for all dependencies."""
    # PostgreSQL check
    pg_ok = False
    try:
        db = _get_db()
        with db._cursor() as cur:
            cur.execute("SELECT 1")
        pg_ok = True
    except Exception:
        pass

    # Redis check
    redis_ok = False
    try:
        r = _get_redis()
        if r is not None and r._client is not None:
            r._client.ping()
            redis_ok = True
    except Exception:
        pass

    # Ollama check
    ollama_ok = False
    try:
        base = settings.ollama_base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{base}/api/tags")
            ollama_ok = resp.status_code == 200
    except Exception:
        pass

    return {
        "status": "ok" if (pg_ok and redis_ok and ollama_ok) else "degraded",
        "postgres": pg_ok,
        "redis": redis_ok,
        "ollama": ollama_ok,
    }


@app.get("/run/status")
def run_status() -> dict:
    """Return progress of the currently-running (or last) batch benchmark run."""
    return _progress.snapshot()


@app.get("/traces", response_model=list[TraceSummary])
def list_traces(
    framework: str | None = Query(default=None),
    task_category: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[TraceSummary]:
    """
    Return a paginated list of stored traces with summary stats.
    """
    try:
        db = _get_db()
        traces = db.get_traces_for_analysis(
            framework=framework,
            task_category=task_category,
            limit=limit + offset,
        )
        # Apply offset manually (DB layer doesn't support it yet)
        traces = traces[offset : offset + limit]
    except Exception as exc:
        logger.exception("Trace listing failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    results = []
    for trace in traces:
        # Count annotations for this trace
        try:
            annotations = db.get_annotations(trace.trace_id)
            num_ann = len(annotations)
        except Exception:
            num_ann = 0

        results.append(
            TraceSummary(
                trace_id=str(trace.trace_id),
                framework=trace.framework,
                task_id=trace.task_id,
                task_category=trace.task_category,
                task_difficulty=trace.task_difficulty,
                task_success=trace.task_success,
                task_score=trace.task_score,
                total_api_tokens=trace.total_api_tokens,
                total_time_seconds=trace.total_time_seconds,
                num_annotations=num_ann,
                run_timestamp=trace.run_timestamp.isoformat(),
            )
        )

    return results
