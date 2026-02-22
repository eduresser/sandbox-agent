"""Cached client instances for LLM, checkpointer, and other services."""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.sqlite import SqliteSaver

from sandbox_agent.settings import get_settings


@lru_cache(maxsize=1)
def get_checkpointer() -> SqliteSaver | None:
    """Return a cached SQLite checkpointer, or None if CHECKPOINT_DB_PATH is empty."""
    settings = get_settings()
    if not settings.CHECKPOINT_DB_PATH:
        return None
    db_path = Path(settings.CHECKPOINT_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    return SqliteSaver(conn)


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
