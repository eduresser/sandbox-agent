"""Cached client instances for LLM, checkpointer, and other services."""

from __future__ import annotations

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from sandbox_agent.settings import get_settings


def get_db_conninfo() -> str:
    """Return a PostgreSQL connection string built from settings."""
    s = get_settings()
    return (
        f"postgresql://{s.POSTGRES_USER}:{s.POSTGRES_PASSWORD}"
        f"@{s.POSTGRES_HOST}:{s.POSTGRES_PORT}/{s.POSTGRES_DB}"
    )


@lru_cache(maxsize=1)
def get_checkpointer() -> PostgresSaver:
    """Return a cached PostgreSQL checkpointer (shared with Aegra)."""
    pool = ConnectionPool(
        conninfo=get_db_conninfo(),
        kwargs={"autocommit": True, "row_factory": dict_row},
        min_size=1,
        max_size=4,
    )
    checkpointer = PostgresSaver(pool)
    checkpointer.setup()
    return checkpointer


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """Return a cached instance of the configured chat model."""
    settings = get_settings()
    kwargs: dict = {
        "model": settings.CHAT_MODEL,
        "model_provider": settings.CHAT_MODEL_PROVIDER,
    }
    if settings.CHAT_MODEL_BASE_URL:
        kwargs["base_url"] = settings.CHAT_MODEL_BASE_URL
    if settings.CHAT_MODEL_API_KEY:
        kwargs["api_key"] = settings.CHAT_MODEL_API_KEY
    return init_chat_model(**kwargs)
