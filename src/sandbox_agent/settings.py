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
    EXECUTION_TIMEOUT_SECONDS: int = 30
    MAX_SESSIONS: int = 5
    TERMINAL_ROOT: bool = False

    # ── Output Limits (characters) ──
    MAX_STDOUT_CHARS: int = 20_000
    MAX_STDERR_CHARS: int = 10_000
    MAX_RESULT_CHARS: int = 20_000
    MAX_TRACEBACK_CHARS: int = 5_000

    # ── Export ──
    OUTPUT_DIR: str = "./outputs"

    # ── Agent ──
    MAX_ITERATIONS: int = 25


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
