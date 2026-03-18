"""Application settings via Pydantic Settings.

Loads from environment variables and ``.env`` file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Sandbox Agent configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM (provider-agnostic) ──
    CHAT_MODEL: str = "gpt-4o"
    CHAT_MODEL_PROVIDER: str = "openai"
    CHAT_MODEL_API_KEY: str
    CHAT_MODEL_BASE_URL: str | None = None
    CHAT_MODEL_SUPPORTS_VISION: bool | None = None

    # ── Container ──
    CONTAINER_MEMORY_LIMIT: str = "2048m"
    CONTAINER_CPU_QUOTA: int = 200_000
    CONTAINER_PIDS_LIMIT: int = 512
    CONTAINER_TMPFS_SIZE: str = "200m"
    CONTAINER_ORPHAN_MIN_AGE_SECONDS: int = 300
    CONTAINER_EXECUTION_TIMEOUT_SECONDS: int = 30
    CONTAINER_MAX_SESSIONS: int = 5
    CONTAINER_MAX_SESSIONS_PER_THREAD: int = 3
    CONTAINER_EXECUTE_AS_ROOT: bool = False
    CONTAINER_NETWORK_ENABLED: bool = True

    # ── Session Lifecycle / GC ──
    SESSION_IDLE_TTL_SECONDS: int = 1800
    SESSION_MAX_LIFETIME_SECONDS: int = 7200
    SESSION_GC_INTERVAL_SECONDS: int = 60
    SESSION_MAX_ACTIVE_THREADS: int = 10

    # ── Output Limits (characters) ──
    MAX_STDOUT_CHARS: int = 50000
    MAX_STDERR_CHARS: int = 120000
    MAX_RESULT_CHARS: int = 30000
    MAX_TRACEBACK_CHARS: int = 8000

    # ── Encryption ──
    ENCRYPTION_KEY: str = ""

    # ── Storage (uploads + exports) ──
    STORAGE_DIR: Path = Path("./storage")
    IMPORT_ALLOWED_DIRS: list[Path] = []

    # ── API (for download URLs in export_files) ──
    API_BASE_URL: str = "http://127.0.0.1:8000"

    # ── Agent ──
    MAX_ITERATIONS: int = 25

    @field_validator("IMPORT_ALLOWED_DIRS", mode="before")
    @classmethod
    def _parse_import_dirs(cls, v: Any) -> list[Path]:
        if isinstance(v, str):
            return [Path(d.strip()) for d in v.split(",") if d.strip()]
        return v

    @model_validator(mode="after")
    def _resolve_paths(self) -> "Settings":
        root = Path(__file__).resolve().parent.parent.parent
        self.STORAGE_DIR = (
            self.STORAGE_DIR.resolve()
            if self.STORAGE_DIR.is_absolute()
            else (root / self.STORAGE_DIR).resolve()
        )
        self.IMPORT_ALLOWED_DIRS = [
            p.resolve() if p.is_absolute() else (root / p).resolve()
            for p in self.IMPORT_ALLOWED_DIRS
        ]
        return self

    # ── Checkpointer (PostgreSQL, shared with Aegra) ──
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
