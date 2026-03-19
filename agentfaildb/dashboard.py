"""
Live monitoring dashboard for AgentFailDB benchmark runs.

Serves on port 1221. Reads stats from PostgreSQL, shows charts/progress,
and provides kill/start buttons for the systemd service.

Run:  python -m agentfaildb.dashboard
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from agentfaildb.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="AgentFailDB Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

TOTAL_FRAMEWORKS = 3
TOTAL_TASKS = 250
TOTAL_RUNS = TOTAL_TASKS * TOTAL_FRAMEWORKS
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
SERVICE_NAME = "agentfaildb.service"


@contextmanager
def _db_conn() -> Generator[Any, None, None]:
    conn = psycopg2.connect(
        settings.postgres_dsn,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        yield conn
    finally:
        conn.close()


def _query_stats() -> dict[str, Any]:
    """Gather all dashboard stats from PostgreSQL."""
    with _db_conn() as conn:
        with conn.cursor() as cur:
            # Overall KPIs
            cur.execute("""
                SELECT
                    COUNT(*) AS total_traces,
                    COUNT(*) FILTER (WHERE task_success IS NOT NULL) AS completed,
                    COUNT(*) FILTER (WHERE task_success = TRUE) AS succeeded,
                    COUNT(*) FILTER (WHERE task_success = FALSE) AS failed
                FROM traces
            """)
            overview = dict(cur.fetchone())

            # Per-framework breakdown
            cur.execute("""
                SELECT
                    framework,
                    COUNT(*) FILTER (WHERE task_success = TRUE) AS succeeded,
                    COUNT(*) FILTER (WHERE task_success = FALSE) AS failed,
                    COUNT(*) FILTER (WHERE task_success IS NOT NULL) AS total
                FROM traces
                WHERE task_success IS NOT NULL
                GROUP BY framework
                ORDER BY framework
            """)
            framework_rows = [dict(r) for r in cur.fetchall()]

            # Per-category breakdown
            cur.execute("""
                SELECT
                    task_category,
                    COUNT(*) AS total
                FROM traces
                WHERE task_success IS NOT NULL
                GROUP BY task_category
                ORDER BY task_category
            """)
            category_rows = [dict(r) for r in cur.fetchall()]

            # Traces over time (hourly buckets)
            cur.execute("""
                SELECT
                    date_trunc('hour', created_at) AS hour,
                    COUNT(*) AS count
                FROM traces
                WHERE task_success IS NOT NULL
                GROUP BY hour
                ORDER BY hour
            """)
            hourly_rows = [
                {"hour": r["hour"].isoformat() if r["hour"] else "", "count": r["count"]}
                for r in cur.fetchall()
            ]

            # Traces per hour rate (last 2 hours)
            cur.execute("""
                SELECT COUNT(*) AS recent
                FROM traces
                WHERE task_success IS NOT NULL
                  AND created_at >= NOW() - INTERVAL '2 hours'
            """)
            recent_count = cur.fetchone()["recent"]
            traces_per_hour = recent_count / 2.0 if recent_count else 0

    completed = overview["completed"]
    remaining = TOTAL_RUNS - completed
    eta_hours = remaining / traces_per_hour if traces_per_hour > 0 else None
    success_rate = 100.0 * overview["succeeded"] / completed if completed > 0 else 0

    return {
        "total_traces": overview["total_traces"],
        "completed": completed,
        "succeeded": overview["succeeded"],
        "failed_count": overview["failed_count"]
        if "failed_count" in overview
        else overview["failed"],
        "total_runs": TOTAL_RUNS,
        "remaining": remaining,
        "success_rate": round(success_rate, 1),
        "traces_per_hour": round(traces_per_hour, 1),
        "eta_hours": round(eta_hours, 1) if eta_hours else None,
        "eta_nights": round(eta_hours / 10, 1) if eta_hours else None,
        "framework_rows": framework_rows,
        "category_rows": category_rows,
        "hourly_rows": hourly_rows,
    }


def _read_log_tail(n: int = 20) -> list[str]:
    """Read last n lines from the most recent benchmark log."""
    try:
        logs = sorted(LOG_DIR.glob("benchmark_*.log"), reverse=True)
        if not logs:
            return ["(no log files found)"]
        text = logs[0].read_text(errors="replace")
        lines = text.strip().splitlines()
        return lines[-n:]
    except Exception as exc:
        return [f"(error reading log: {exc})"]


def _service_is_active() -> bool:
    """Check if the systemd user service is currently running."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    try:
        stats = _query_stats()
    except Exception as exc:
        logger.error("Failed to query stats: %s", exc)
        stats = {
            "total_traces": 0,
            "completed": 0,
            "succeeded": 0,
            "failed_count": 0,
            "total_runs": TOTAL_RUNS,
            "remaining": TOTAL_RUNS,
            "success_rate": 0,
            "traces_per_hour": 0,
            "eta_hours": None,
            "eta_nights": None,
            "framework_rows": [],
            "category_rows": [],
            "hourly_rows": [],
        }

    log_lines = _read_log_tail()
    service_active = _service_is_active()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "log_lines": log_lines,
            "service_active": service_active,
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


@app.post("/api/stop")
async def stop_service() -> JSONResponse:
    """Kill switch: stop the benchmark service."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "stop", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return JSONResponse({"status": "stopped", "message": "Service stopped."})
        return JSONResponse(
            {"status": "error", "message": result.stderr.strip()},
            status_code=500,
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.post("/api/start")
async def start_service() -> JSONResponse:
    """Start the benchmark service."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return JSONResponse({"status": "started", "message": "Service started."})
        return JSONResponse(
            {"status": "error", "message": result.stderr.strip()},
            status_code=500,
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.get("/api/stats")
async def api_stats() -> JSONResponse:
    """JSON endpoint for programmatic access to stats."""
    try:
        stats = _query_stats()
        return JSONResponse(stats)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=1221)
