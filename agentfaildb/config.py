"""
Central configuration for AgentFailDB.

All values are read from environment variables (with .env file support via
python-dotenv).  Import the module-level ``settings`` singleton everywhere
instead of constructing Settings() directly.
"""

from __future__ import annotations

from typing import Optional

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings resolved from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Ollama ──────────────────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11435/v1"
    task_model: str = "llama3.1:8b"
    annotation_model: str = "llama3.1:8b"
    ollama_num_ctx: int = 8192

    # ── PostgreSQL ───────────────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "agentfaildb"
    postgres_user: str = "agentfaildb"
    postgres_password: str = "agentfaildb"

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # ── Anthropic (optional) ─────────────────────────────────────────────────
    anthropic_api_key: Optional[str] = None

    # ── Benchmark settings ───────────────────────────────────────────────────
    run_timeout_seconds: int = 120
    max_tokens_per_run: int = 10_000
    max_messages_per_run: int = 20
    resource_exhaustion_multiplier: float = 3.0

    # ── Annotation ───────────────────────────────────────────────────────────
    annotation_passes: int = 3
    annotation_confidence_threshold: float = 0.5

    # ── Derived properties ───────────────────────────────────────────────────

    @computed_field  # type: ignore[misc]
    @property
    def postgres_dsn(self) -> str:
        """psycopg2-compatible connection string."""
        return (
            f"host={self.postgres_host} "
            f"port={self.postgres_port} "
            f"dbname={self.postgres_db} "
            f"user={self.postgres_user} "
            f"password={self.postgres_password}"
        )

    # ── Class-level constants ────────────────────────────────────────────────

    @classmethod
    def content_message_types(cls) -> set[str]:
        """
        Return the set of MessageType string values that represent content
        messages (i.e. included in failure detection by default).

        Defined here rather than in trace.py to avoid circular imports when
        config is imported early in the application boot sequence.
        """
        return {
            "task_delegation",
            "response",
            "feedback",
            "tool_call",
            "tool_result",
        }


# Module-level singleton — import this everywhere.
settings = Settings()
