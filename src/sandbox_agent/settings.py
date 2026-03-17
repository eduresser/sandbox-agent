"""Application settings via Pydantic Settings.

Loads from environment variables and ``.env`` file.
"""

from __future__ import annotations

from functools import lru_cache

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
    CONTAINER_READ_ONLY_ROOTFS: bool = False
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
    STORAGE_DIR: str = "./storage"
    IMPORT_ALLOWED_DIRS: str = ""

    # ── API (for download URLs in export_files) ──
    API_BASE_URL: str = "http://127.0.0.1:8000"

    # ── Agent ──
    MAX_ITERATIONS: int = 25

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
