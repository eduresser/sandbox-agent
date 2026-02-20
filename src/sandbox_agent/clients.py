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
    kwargs: dict = {
        "model": settings.CHAT_MODEL,
        "model_provider": settings.CHAT_MODEL_PROVIDER,
    }
    if settings.CHAT_MODEL_BASE_URL:
        kwargs["base_url"] = settings.CHAT_MODEL_BASE_URL
    if settings.CHAT_MODEL_API_KEY:
        kwargs["api_key"] = settings.CHAT_MODEL_API_KEY
    return init_chat_model(**kwargs)


def reset_clients_cache() -> None:
    """Clear all cached client instances."""
    get_chat_model.cache_clear()
