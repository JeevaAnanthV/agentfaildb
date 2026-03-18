"""
PostgreSQL and Redis clients for AgentFailDB trace storage and caching.

Database  — synchronous psycopg2 client; used by the harness runner after each
            completed framework run to persist traces, messages, and annotations.
RedisClient — thin wrapper for pattern-signature caching and rolling resource-
              exhaustion baselines.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import psycopg2
import psycopg2.extras
import redis as redis_lib

from agentfaildb.config import settings
from agentfaildb.trace import (
    AgentMessage,
    AnnotationSource,
    FailureAnnotation,
    FailureCategory,
    FailureSeverity,
    GroundTruthType,
    MessageType,
    TaskTrace,
)

logger = logging.getLogger(__name__)

# Register UUID adapter so psycopg2 serialises uuid.UUID objects natively.
psycopg2.extras.register_uuid()


# ── Database ──────────────────────────────────────────────────────────────────


class Database:
    """Synchronous PostgreSQL client for trace storage."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn: str = dsn if dsn is not None else settings.postgres_dsn
        self._conn: psycopg2.extensions.connection | None = None

    # ── Connection management ─────────────────────────────────────────────────

    def connect(self) -> None:
        """Open a database connection."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = False
            logger.debug("Database connection established")

    def disconnect(self) -> None:
        """Close the database connection gracefully."""
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
            logger.debug("Database connection closed")
        self._conn = None

    def __enter__(self) -> Database:
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.disconnect()

    def _cursor(self) -> psycopg2.extensions.cursor:
        if self._conn is None or self._conn.closed:
            raise RuntimeError("Database.connect() must be called before executing queries")
        return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── Write operations ──────────────────────────────────────────────────────

    def insert_trace(self, trace: TaskTrace) -> UUID:
        """
        Insert trace metadata into the ``traces`` table.

        Returns the ``trace_id`` (same as ``trace.trace_id``).
        Messages and annotations are written separately.
        """
        row = trace.to_db_dict()
        sql = """
            INSERT INTO traces (
                trace_id, framework, task_category, task_difficulty, task_id,
                task_description, ground_truth_type, ground_truth, actual_output,
                total_api_tokens, total_content_tokens, context_overhead_ratio,
                total_time_seconds, num_agents, agent_roles,
                task_success, task_score, task_success_method,
                model_used, run_timestamp, run_config
            ) VALUES (
                %(trace_id)s, %(framework)s, %(task_category)s, %(task_difficulty)s,
                %(task_id)s, %(task_description)s, %(ground_truth_type)s,
                %(ground_truth)s, %(actual_output)s,
                %(total_api_tokens)s, %(total_content_tokens)s,
                %(context_overhead_ratio)s, %(total_time_seconds)s,
                %(num_agents)s, %(agent_roles)s,
                %(task_success)s, %(task_score)s, %(task_success_method)s,
                %(model_used)s, %(run_timestamp)s, %(run_config)s
            )
            ON CONFLICT (trace_id) DO NOTHING
        """
        # psycopg2 needs JSONB values as Json-adapted objects
        params = dict(row)
        params["ground_truth"] = (
            psycopg2.extras.Json(row["ground_truth"]) if row["ground_truth"] is not None else None
        )
        params["agent_roles"] = psycopg2.extras.Json(row["agent_roles"])
        params["run_config"] = psycopg2.extras.Json(row["run_config"])

        with self._cursor() as cur:
            cur.execute(sql, params)
        self._conn.commit()  # type: ignore[union-attr]
        logger.debug("Inserted trace %s", trace.trace_id)
        return trace.trace_id

    def insert_messages(self, trace_id: UUID, messages: list[AgentMessage]) -> None:
        """Batch-insert all messages for a trace using executemany."""
        if not messages:
            return

        sql = """
            INSERT INTO messages (
                message_id, trace_id, message_index, timestamp,
                source_agent, target_agent, content, msg_type,
                api_token_count, content_token_count, model_used,
                tool_calls, metadata
            ) VALUES (
                %(message_id)s, %(trace_id)s, %(message_index)s, %(timestamp)s,
                %(source_agent)s, %(target_agent)s, %(content)s, %(msg_type)s,
                %(api_token_count)s, %(content_token_count)s, %(model_used)s,
                %(tool_calls)s, %(metadata)s
            )
            ON CONFLICT (message_id) DO NOTHING
        """
        params_list = [
            {
                "message_id": msg.message_id,
                "trace_id": trace_id,
                "message_index": msg.message_index,
                "timestamp": msg.timestamp,
                "source_agent": msg.source_agent,
                "target_agent": msg.target_agent,
                "content": msg.content,
                "msg_type": msg.message_type.value,
                "api_token_count": msg.api_token_count,
                "content_token_count": msg.content_token_count,
                "model_used": msg.model_used,
                "tool_calls": psycopg2.extras.Json(msg.tool_calls) if msg.tool_calls else None,
                "metadata": psycopg2.extras.Json(msg.metadata),
            }
            for msg in messages
        ]

        with self._cursor() as cur:
            cur.executemany(sql, params_list)
        self._conn.commit()  # type: ignore[union-attr]
        logger.debug("Inserted %d messages for trace %s", len(messages), trace_id)

    def insert_annotation(self, annotation: FailureAnnotation) -> UUID:
        """Insert one failure annotation.  Returns its annotation_id."""
        sql = """
            INSERT INTO annotations (
                annotation_id, trace_id, category, severity,
                root_cause_agent, failure_point_index, description,
                confidence, source, annotator_id, created_at
            ) VALUES (
                %(annotation_id)s, %(trace_id)s, %(category)s, %(severity)s,
                %(root_cause_agent)s, %(failure_point_index)s, %(description)s,
                %(confidence)s, %(source)s, %(annotator_id)s, %(created_at)s
            )
            ON CONFLICT (annotation_id) DO NOTHING
        """
        params = {
            "annotation_id": annotation.annotation_id,
            "trace_id": annotation.trace_id,
            "category": annotation.category.value,
            "severity": annotation.severity.value,
            "root_cause_agent": annotation.root_cause_agent,
            "failure_point_index": annotation.failure_point_index,
            "description": annotation.description,
            "confidence": annotation.confidence,
            "source": annotation.source.value,
            "annotator_id": annotation.annotator_id,
            "created_at": annotation.created_at,
        }
        with self._cursor() as cur:
            cur.execute(sql, params)
        self._conn.commit()  # type: ignore[union-attr]
        return annotation.annotation_id

    # ── Read operations ───────────────────────────────────────────────────────

    def get_trace(self, trace_id: UUID) -> TaskTrace | None:
        """Fetch a complete trace with all its messages (annotations not included)."""
        trace_sql = "SELECT * FROM traces WHERE trace_id = %s"
        messages_sql = "SELECT * FROM messages WHERE trace_id = %s ORDER BY message_index ASC"

        with self._cursor() as cur:
            cur.execute(trace_sql, (trace_id,))
            trace_row = cur.fetchone()
            if trace_row is None:
                return None

            cur.execute(messages_sql, (trace_id,))
            message_rows = cur.fetchall()

        return self._build_trace(dict(trace_row), [dict(r) for r in message_rows])

    def get_annotations(self, trace_id: UUID) -> list[FailureAnnotation]:
        """Fetch all annotations for a trace ordered by creation time."""
        sql = "SELECT * FROM annotations WHERE trace_id = %s ORDER BY created_at ASC"
        with self._cursor() as cur:
            cur.execute(sql, (trace_id,))
            rows = cur.fetchall()
        return [self._build_annotation(dict(r)) for r in rows]

    def get_traces_for_analysis(
        self,
        framework: str | None = None,
        task_category: str | None = None,
        limit: int = 100,
    ) -> list[TaskTrace]:
        """
        Query traces with optional filters.

        Messages are fetched for each trace via a single batched query to
        avoid N+1 round trips.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if framework is not None:
            conditions.append("t.framework = %s")
            params.append(framework)
        if task_category is not None:
            conditions.append("t.task_category = %s")
            params.append(task_category)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        traces_sql = f"SELECT * FROM traces t {where} ORDER BY run_timestamp DESC LIMIT %s"
        params.append(limit)

        with self._cursor() as cur:
            cur.execute(traces_sql, params)
            trace_rows = [dict(r) for r in cur.fetchall()]

        if not trace_rows:
            return []

        trace_ids = [r["trace_id"] for r in trace_rows]
        # Fetch all messages for the result set in one query
        placeholders = ",".join(["%s"] * len(trace_ids))
        messages_sql = (
            f"SELECT * FROM messages WHERE trace_id IN ({placeholders}) "
            f"ORDER BY trace_id, message_index ASC"
        )
        with self._cursor() as cur:
            cur.execute(messages_sql, trace_ids)
            all_message_rows = [dict(r) for r in cur.fetchall()]

        # Group messages by trace_id
        messages_by_trace: dict[UUID, list[dict[str, Any]]] = {tid: [] for tid in trace_ids}
        for mrow in all_message_rows:
            tid = mrow["trace_id"]
            if tid in messages_by_trace:
                messages_by_trace[tid].append(mrow)

        return [
            self._build_trace(trow, messages_by_trace.get(trow["trace_id"], []))
            for trow in trace_rows
        ]

    # ── Private builders ──────────────────────────────────────────────────────

    @staticmethod
    def _build_trace(row: dict[str, Any], message_rows: list[dict[str, Any]]) -> TaskTrace:
        messages = [
            AgentMessage(
                message_id=r["message_id"],
                trace_id=r["trace_id"],
                message_index=r["message_index"],
                timestamp=r["timestamp"],
                source_agent=r["source_agent"],
                target_agent=r["target_agent"],
                content=r["content"],
                message_type=MessageType(r["msg_type"]),
                api_token_count=r.get("api_token_count"),
                content_token_count=r.get("content_token_count"),
                model_used=r.get("model_used"),
                tool_calls=r["tool_calls"] if r.get("tool_calls") else [],
                metadata=r["metadata"] if r.get("metadata") else {},
            )
            for r in message_rows
        ]

        return TaskTrace(
            trace_id=row["trace_id"],
            framework=row["framework"],
            task_category=row["task_category"],
            task_difficulty=row["task_difficulty"],
            task_id=row["task_id"],
            task_description=row["task_description"],
            ground_truth_type=GroundTruthType(row["ground_truth_type"]),
            ground_truth=row.get("ground_truth"),
            actual_output=row["actual_output"],
            messages=messages,
            total_time_seconds=row["total_time_seconds"],
            num_agents=row["num_agents"],
            agent_roles=row["agent_roles"],
            task_success=row.get("task_success"),
            task_score=row.get("task_score"),
            task_success_method=row.get("task_success_method"),
            model_used=row["model_used"],
            run_timestamp=row["run_timestamp"],
            run_config=row.get("run_config") or {},
        )

    @staticmethod
    def _build_annotation(row: dict[str, Any]) -> FailureAnnotation:
        return FailureAnnotation(
            annotation_id=row["annotation_id"],
            trace_id=row["trace_id"],
            category=FailureCategory(row["category"]),
            severity=FailureSeverity(row["severity"]),
            root_cause_agent=row.get("root_cause_agent"),
            failure_point_index=row.get("failure_point_index"),
            description=row["description"],
            confidence=row["confidence"],
            source=AnnotationSource(row["source"]),
            annotator_id=row.get("annotator_id"),
            created_at=row["created_at"],
        )


# ── RedisClient ───────────────────────────────────────────────────────────────

# Hardcoded defaults returned when fewer than 20 baseline samples exist.
_BASELINE_DEFAULTS: dict[str, Any] = {
    "tokens": 10_000,
    "time_s": 120.0,
    "messages": 30,
}
_MIN_BASELINE_SAMPLES = 20


class RedisClient:
    """
    Redis client for pattern-signature caching and rolling resource-exhaustion
    baselines.

    Pattern signatures are stored as JSON strings with a configurable TTL.
    Baselines are stored as Redis sorted sets (one per metric per
    category+difficulty combination) so that the median can be computed
    efficiently by sorted-set rank queries.
    """

    def __init__(self) -> None:
        self._client: redis_lib.Redis | None = None  # type: ignore[type-arg]

    def connect(self) -> None:
        """Create the Redis connection pool."""
        if self._client is None:
            self._client = redis_lib.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                decode_responses=True,
            )
            logger.debug("Redis connection established")

    def disconnect(self) -> None:
        """Close the Redis connection."""
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.debug("Redis connection closed")

    def _r(self) -> redis_lib.Redis:  # type: ignore[type-arg]
        if self._client is None:
            raise RuntimeError("RedisClient.connect() must be called first")
        return self._client

    # ── Pattern signature caching ─────────────────────────────────────────────

    def cache_pattern_signature(self, key: str, value: dict[str, Any], ttl: int = 3600) -> None:
        """Serialise *value* to JSON and store it under *key* with a TTL."""
        self._r().setex(f"pattern:{key}", ttl, json.dumps(value))

    def get_pattern_signature(self, key: str) -> dict[str, Any] | None:
        """Return the cached pattern signature or None if expired / not found."""
        raw = self._r().get(f"pattern:{key}")
        if raw is None:
            return None
        return json.loads(raw)  # type: ignore[arg-type]

    # ── Rolling resource-exhaustion baselines ─────────────────────────────────

    def add_baseline_sample(
        self,
        category: str,
        difficulty: str,
        tokens: int,
        time_s: float,
        messages: int,
    ) -> None:
        """
        Record a successful run's metrics in the rolling baseline sorted sets.

        Three sorted sets are maintained per (category, difficulty) pair:
          baseline:<category>:<difficulty>:tokens
          baseline:<category>:<difficulty>:time_s
          baseline:<category>:<difficulty>:messages

        Each member is a unique float-string timestamp so that the sorted set
        acts as a time-ordered series from which the median can be retrieved.
        """
        import time as _time

        prefix = f"baseline:{category}:{difficulty}"
        ts = str(_time.time())
        pipe = self._r().pipeline()
        pipe.zadd(f"{prefix}:tokens", {ts: tokens})
        pipe.zadd(f"{prefix}:time_s", {ts: time_s})
        pipe.zadd(f"{prefix}:messages", {ts: messages})
        pipe.execute()

    def get_baseline(self, category: str, difficulty: str) -> dict[str, Any]:
        """
        Return the median tokens / time_s / messages for *category* + *difficulty*.

        Falls back to hardcoded defaults when fewer than ``_MIN_BASELINE_SAMPLES``
        samples exist in any of the sorted sets.
        """
        prefix = f"baseline:{category}:{difficulty}"

        def _median(key: str) -> float | None:
            count = self._r().zcard(key)
            if count < _MIN_BASELINE_SAMPLES:
                return None
            mid = count // 2
            scores = self._r().zrange(key, mid, mid, withscores=True)
            if not scores:
                return None
            return scores[0][1]

        tokens_med = _median(f"{prefix}:tokens")
        time_med = _median(f"{prefix}:time_s")
        messages_med = _median(f"{prefix}:messages")

        if tokens_med is None or time_med is None or messages_med is None:
            return dict(_BASELINE_DEFAULTS)

        return {
            "tokens": int(tokens_med),
            "time_s": float(time_med),
            "messages": int(messages_med),
        }
