"""
Live monitoring dashboard for AgentFailDB benchmark runs.

Serves on port 1221. Reads stats from PostgreSQL, shows live system
resources (CPU, RAM, GPU, disk, Docker), and provides kill/start controls.

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

import psutil
import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from agentfaildb.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="AgentFailDB War Room")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

TOTAL_FRAMEWORKS = 3
TOTAL_TASKS = 250
TOTAL_RUNS = TOTAL_TASKS * TOTAL_FRAMEWORKS
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
SERVICE_NAME = "agentfaildb.service"


# ── Database helpers ─────────────────────────────────────────────────────────


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
            cur.execute("""
                SELECT
                    COUNT(*) AS total_traces,
                    COUNT(*) FILTER (WHERE task_success IS NOT NULL) AS completed,
                    COUNT(*) FILTER (WHERE task_success = TRUE) AS succeeded,
                    COUNT(*) FILTER (WHERE task_success = FALSE) AS failed
                FROM traces
            """)
            overview = dict(cur.fetchone())

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

            cur.execute("""
                SELECT task_category, COUNT(*) AS total
                FROM traces
                WHERE task_success IS NOT NULL
                GROUP BY task_category
                ORDER BY task_category
            """)
            category_rows = [dict(r) for r in cur.fetchall()]

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


# ── System resource helpers ──────────────────────────────────────────────────


def _get_gpu_stats() -> dict[str, Any] | None:
    """Query nvidia-smi for GPU stats. Returns None if unavailable."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,"
                "temperature.gpu,name,power.draw,power.limit",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        parts = [p.strip().strip("[]") for p in result.stdout.strip().split(",")]

        def _safe_float(val: str) -> float:
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        return {
            "gpu_util": int(_safe_float(parts[0])),
            "mem_util": int(_safe_float(parts[1])),
            "mem_used_mb": int(_safe_float(parts[2])),
            "mem_total_mb": int(_safe_float(parts[3])),
            "temp_c": int(_safe_float(parts[4])),
            "name": parts[5] if len(parts) > 5 else "GPU",
            "power_w": _safe_float(parts[6]) if len(parts) > 6 else 0,
            "power_limit_w": _safe_float(parts[7]) if len(parts) > 7 else 0,
        }
    except Exception:
        return None


def _get_docker_stats() -> list[dict[str, str]]:
    """Get running Docker container stats."""
    try:
        result = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        containers = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                containers.append(
                    {
                        "name": parts[0],
                        "cpu": parts[1],
                        "mem_usage": parts[2],
                        "mem_pct": parts[3],
                    }
                )
        return containers
    except Exception:
        return []


def _get_system_stats() -> dict[str, Any]:
    """Gather CPU, RAM, disk, network, GPU, Docker stats."""
    cpu_per_core = psutil.cpu_percent(interval=0.3, percpu=True)
    cpu_overall = sum(cpu_per_core) / len(cpu_per_core) if cpu_per_core else 0
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    swap = psutil.swap_memory()

    # Uptime
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot_time
    uptime_str = f"{uptime.days}d {uptime.seconds // 3600}h {(uptime.seconds % 3600) // 60}m"

    return {
        "cpu_overall": round(cpu_overall, 1),
        "cpu_per_core": [round(c, 1) for c in cpu_per_core],
        "cpu_count": psutil.cpu_count(),
        "ram_used_gb": round(mem.used / (1024**3), 1),
        "ram_total_gb": round(mem.total / (1024**3), 1),
        "ram_pct": mem.percent,
        "swap_used_gb": round(swap.used / (1024**3), 1),
        "swap_total_gb": round(swap.total / (1024**3), 1),
        "swap_pct": swap.percent,
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
        "disk_pct": disk.percent,
        "net_sent_gb": round(net.bytes_sent / (1024**3), 2),
        "net_recv_gb": round(net.bytes_recv / (1024**3), 2),
        "uptime": uptime_str,
        "gpu": _get_gpu_stats(),
        "docker": _get_docker_stats(),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _read_log_tail(n: int = 25) -> list[str]:
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


def _get_next_timer_fire() -> str | None:
    """Get the next scheduled fire time for the agentfaildb timer."""
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                "agentfaildb.timer",
                "--property=NextElapseUSecRealtime",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            val = result.stdout.strip().split("=", 1)[-1]
            if val and val != "n/a":
                return val
    except Exception:
        pass
    return None


# ── Routes ───────────────────────────────────────────────────────────────────


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
    next_fire = _get_next_timer_fire()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "log_lines": log_lines,
            "service_active": service_active,
            "next_fire": next_fire,
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


@app.get("/api/system")
async def api_system() -> JSONResponse:
    """Live system resource stats — polled every 3s by the frontend."""
    try:
        data = _get_system_stats()
        data["service_active"] = _service_is_active()
        data["timestamp"] = datetime.now().strftime("%H:%M:%S")
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


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
    """JSON endpoint for programmatic access to benchmark stats."""
    try:
        stats = _query_stats()
        return JSONResponse(stats)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=1221)
