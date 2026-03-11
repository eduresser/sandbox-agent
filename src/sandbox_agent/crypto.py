"""Symmetric encryption for sensitive settings stored at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with an auto-generated key stored
in ``{STORAGE_DIR}/.encryption_key``.  The key file is created with mode
0600 on first use.
"""

from __future__ import annotations

import json
import logging
import os
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


def _key_file() -> Path:
    return _storage_dir() / ".encryption_key"


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    key_path = _key_file()
    if key_path.exists():
        key = key_path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
    return Fernet(key)


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
