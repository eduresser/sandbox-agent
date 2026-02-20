"""Cached client instances for LLM and other services."""

from __future__ import annotations

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from sandbox_agent.settings import get_settings


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """Return a cached instance of the configured chat model."""
    settings = get_settings()
    return init_chat_model(
        settings.CHAT_MODEL,
        api_key=settings.OPENAI_API_KEY,
    )


def reset_clients_cache() -> None:
    """Clear all cached client instances."""
    get_chat_model.cache_clear()
