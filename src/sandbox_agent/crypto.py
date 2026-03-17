"""Symmetric encryption for sensitive settings stored at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with a key provided via the
``ENCRYPTION_KEY`` environment variable / settings.  If absent, a new key
is generated automatically and logged as a warning so the operator can
persist it.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:"


def _storage_dir() -> Path:
    from sandbox_agent.settings import get_settings

    sd = Path(get_settings().STORAGE_DIR)
    if not sd.is_absolute():
        sd = Path(__file__).resolve().parent.parent.parent / sd
    return sd


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    from sandbox_agent.settings import get_settings

    key = get_settings().ENCRYPTION_KEY
    if not key:
        key = Fernet.generate_key().decode("ascii")
        logger.warning(
            "ENCRYPTION_KEY not set — generated ephemeral key. "
            "Add ENCRYPTION_KEY=%s to your .env to persist encrypted data across restarts.",
            key,
        )
    return Fernet(key.encode("ascii") if isinstance(key, str) else key)


# ── Public helpers ──────────────────────────────────────────────────────


def encrypt_value(plaintext: str) -> str:
    """Encrypt *plaintext* and return a prefixed token string."""
    if not plaintext:
        return ""
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return f"{_ENC_PREFIX}{token.decode('ascii')}"


def decrypt_value(stored: str) -> str:
    """Decrypt a value previously produced by :func:`encrypt_value`.

    Unencrypted strings (legacy migration path) are returned as-is.
    """
    if not stored:
        return ""
    if not stored.startswith(_ENC_PREFIX):
        return stored
    token = stored[len(_ENC_PREFIX) :].encode("ascii")
    try:
        return _get_fernet().decrypt(token).decode("utf-8")
    except InvalidToken:
        logger.warning("Failed to decrypt stored value — returning empty")
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(_ENC_PREFIX)


def mask_api_key(key: str) -> str:
    """Return a masked representation suitable for display (e.g. ``sk-••••a1b2``)."""
    if not key or is_encrypted(key):
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    prefix = key[:3] if key[:3].isascii() else ""
    suffix = key[-4:]
    hidden = len(key) - len(prefix) - len(suffix)
    return f"{prefix}{'•' * hidden}{suffix}"


# ── API key resolution ──────────────────────────────────────────────────


def resolve_api_key() -> str:
    """Best-effort resolution: encrypted frontend settings → env var fallback."""
    path = _storage_dir() / "frontend_settings.json"
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
            stored = data.get("chatModelApiKey", "")
            if stored:
                decrypted = decrypt_value(stored)
                if decrypted:
                    return decrypted
        except (json.JSONDecodeError, OSError):
            pass

    from sandbox_agent.settings import get_settings

    return get_settings().CHAT_MODEL_API_KEY
