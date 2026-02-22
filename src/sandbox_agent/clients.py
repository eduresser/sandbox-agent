"""Cached client instances for LLM, checkpointer, and other services."""

from __future__ import annotations

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from sandbox_agent.settings import get_settings


@lru_cache(maxsize=1)
def get_checkpointer() -> PostgresSaver:
    """Return a cached PostgreSQL checkpointer (shared with Aegra)."""
    settings = get_settings()
    checkpoint_db_url = (
        f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )
    pool = ConnectionPool(
        conninfo=checkpoint_db_url,
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
