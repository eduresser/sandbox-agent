"""Symmetric encryption for settings stored at rest (PostgreSQL).

Uses Fernet (AES-128-CBC + HMAC-SHA256) with a key provided via the
``ENCRYPTION_KEY`` environment variable / settings.  If absent, a new key
is generated automatically and logged as a warning so the operator can
persist it.

Settings are stored as an encrypted blob in a single-row PostgreSQL table,
removing the need for any local file.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_SETTINGS_TABLE = "frontend_settings"


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


# ── Encryption primitives ──────────────────────────────────────────────


def encrypt_json(data: dict[str, Any]) -> bytes:
    """Serialize *data* to JSON and return the Fernet-encrypted bytes."""
    plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return _get_fernet().encrypt(plaintext)


def decrypt_json(token: bytes) -> dict[str, Any]:
    """Decrypt a Fernet token and return the parsed JSON dict."""
    plaintext = _get_fernet().decrypt(token)
    return json.loads(plaintext.decode("utf-8"))


# ── Database-backed settings ───────────────────────────────────────────


def _ensure_table() -> None:
    from sandbox_agent.clients.infra import get_db_pool

    with get_db_pool().connection() as conn:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_SETTINGS_TABLE} ("
            "  id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),"
            "  data TEXT NOT NULL"
            ")"
        )


_table_ready = False


def _setup_once() -> None:
    global _table_ready
    if not _table_ready:
        _ensure_table()
        _table_ready = True


def load_encrypted_settings() -> dict[str, Any]:
    """Load and decrypt frontend settings from PostgreSQL."""
    _setup_once()

    from sandbox_agent.clients.infra import get_db_pool

    with get_db_pool().connection() as conn:
        row = conn.execute(
            f"SELECT data FROM {_SETTINGS_TABLE} WHERE id = 1"
        ).fetchone()

    if not row:
        return {}
    try:
        return decrypt_json(row["data"].encode("ascii"))
    except (InvalidToken, json.JSONDecodeError, KeyError):
        logger.warning("Failed to decrypt frontend settings from DB", exc_info=True)
        return {}


def save_encrypted_settings(data: dict[str, Any]) -> None:
    """Encrypt *data* and upsert into PostgreSQL."""
    _setup_once()
    token = encrypt_json(data).decode("ascii")

    from sandbox_agent.clients.infra import get_db_pool

    with get_db_pool().connection() as conn:
        conn.execute(
            f"INSERT INTO {_SETTINGS_TABLE} (id, data) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
            (token,),
        )


# ── Helpers ────────────────────────────────────────────────────────────


def mask_api_key(key: str) -> str:
    """Return a masked representation suitable for display (e.g. ``sk-••••a1b2``)."""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    prefix = key[:3] if key[:3].isascii() else ""
    suffix = key[-4:]
    hidden = len(key) - len(prefix) - len(suffix)
    return f"{prefix}{'•' * hidden}{suffix}"


# ── API key resolution ─────────────────────────────────────────────────


def resolve_api_key() -> str:
    """Best-effort resolution: encrypted DB settings → env var fallback."""
    try:
        data = load_encrypted_settings()
        stored = data.get("chatModelApiKey", "")
        if stored:
            return stored
    except Exception:
        logger.debug("Could not load settings from DB for API key resolution", exc_info=True)

    from sandbox_agent.settings import get_settings

    return get_settings().CHAT_MODEL_API_KEY
