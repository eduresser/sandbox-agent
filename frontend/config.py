"""Persistent, secure storage for frontend settings.

- Non-sensitive config (model, provider, base_url, vision) → JSON file
- API key → keyring (OS keychain) when available, else config file with 0o600
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "sandbox-agent-frontend"
_KEYRING_USERNAME = "api_key"

_CONFIG_KEYS = (
    "chat_model",
    "chat_model_provider",
    "chat_model_base_url",
    "chat_model_supports_vision",
)


def _config_path() -> Path:
    """Return path to frontend config file (XDG-compliant)."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "sandbox-agent" / "frontend-config.json"


def _ensure_config_dir() -> Path:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _try_keyring_get() -> str | None:
    """Get API key from keyring. Returns None if keyring unavailable or empty."""
    try:
        import keyring

        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception as e:
        logger.debug("keyring get failed: %s", e)
        return None


def _try_keyring_set(api_key: str) -> bool:
    """Store API key in keyring. Returns False if keyring unavailable."""
    try:
        import keyring

        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, api_key)
        return True
    except Exception as e:
        logger.debug("keyring set failed: %s", e)
        return False


def _try_keyring_delete() -> bool:
    """Delete API key from keyring."""
    try:
        import keyring

        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        return True
    except Exception as e:
        logger.debug("keyring delete failed: %s", e)
        return False


def load_config() -> dict[str, Any]:
    """Load persisted config. Returns dict with keys from _CONFIG_KEYS + chat_model_api_key."""
    result: dict[str, Any] = {}

    # 1. Load from JSON file
    path = _config_path()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for k in _CONFIG_KEYS:
                if k in data:
                    result[k] = data[k]
            # Fallback: API key in file (when keyring was unavailable at save time)
            if "chat_model_api_key" in data:
                result["chat_model_api_key"] = data["chat_model_api_key"]
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not load config from %s: %s", path, e)

    # 2. Prefer keyring for API key
    api_key = _try_keyring_get()
    if api_key:
        result["chat_model_api_key"] = api_key
    # else: use file value if present (from fallback)

    return result


def save_config(config: dict[str, Any]) -> None:
    """Persist config. API key goes to keyring when available."""
    path = _ensure_config_dir()

    # Build JSON payload (exclude API key from file when using keyring)
    to_file: dict[str, Any] = {}
    for k in _CONFIG_KEYS:
        if k in config:
            to_file[k] = config[k]

    api_key = config.get("chat_model_api_key", "")
    used_keyring = False
    if api_key:
        used_keyring = _try_keyring_set(api_key)
    else:
        _try_keyring_delete()

    # Fallback: store API key in file if keyring failed
    if api_key and not used_keyring:
        to_file["chat_model_api_key"] = api_key

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(to_file, f, indent=2)
        path.chmod(0o600)  # Restrict to owner only
    except OSError as e:
        logger.warning("Could not save config to %s: %s", path, e)
