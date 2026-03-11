"""Configurable schema for the Sandbox Agent graph.

Defines fields that callers (frontend, LangGraph Platform, etc.) can override
at runtime via ``config["configurable"]``.  Defaults are pulled from
application settings so the agent works out-of-the-box without explicit
configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional

from langchain_core.runnables import RunnableConfig, ensure_config

from sandbox_agent.settings import get_settings


def _setting_default(attr: str):
    """Return a factory that lazily reads *attr* from application settings."""
    def factory():
        return getattr(get_settings(), attr)
    return factory


@dataclass(kw_only=True)
class Configuration:
    """Runtime-configurable parameters for the Sandbox Agent.

    Every field maps to a key in ``config["configurable"]`` and falls back to
    the corresponding application setting when not provided by the caller.
    """

    chat_model: str = field(
        default_factory=_setting_default("CHAT_MODEL"),
        metadata={"description": "LLM model name (e.g. 'gpt-4o', 'claude-sonnet-4-20250514')."},
    )
    chat_model_provider: str = field(
        default_factory=_setting_default("CHAT_MODEL_PROVIDER"),
        metadata={"description": "LLM provider (e.g. 'openai', 'anthropic')."},
    )
    chat_model_api_key: Optional[str] = field(  # noqa: UP007
        default_factory=_setting_default("CHAT_MODEL_API_KEY"),
        metadata={"description": "API key for the LLM provider."},
    )
    chat_model_base_url: Optional[str] = field(  # noqa: UP007
        default_factory=_setting_default("CHAT_MODEL_BASE_URL"),
        metadata={"description": "Custom base URL for the LLM API endpoint."},
    )
    chat_model_supports_vision: Optional[bool] = field(  # noqa: UP007
        default_factory=_setting_default("CHAT_MODEL_SUPPORTS_VISION"),
        metadata={"description": "Whether the model supports vision/image inputs."},
    )

    @classmethod
    def from_runnable_config(cls, config: RunnableConfig | None = None) -> Configuration:
        """Instantiate from a LangGraph ``RunnableConfig``.

        Only keys that match dataclass field names are forwarded; unknown keys
        are silently ignored so callers can pass ``thread_id`` and other
        LangGraph-internal keys without issues.
        """
        config = ensure_config(config)
        configurable = config.get("configurable", {})
        field_names = {f.name for f in fields(cls)}

        init_kwargs: dict = {}
        for key, value in configurable.items():
            if key not in field_names:
                continue
            if key == "chat_model_supports_vision" and value is not None:
                if isinstance(value, str):
                    value = value.lower() in ("true", "1", "yes")
            init_kwargs[key] = value

        return cls(**init_kwargs)
