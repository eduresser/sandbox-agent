"""Shared helpers for tool error responses.

Provides a single ``error_payload`` that returns a ``dict`` (used by MCP and
LangChain tools alike) and a thin ``error_response`` wrapper that serialises
the payload to a JSON string for LangChain tools.
"""

from __future__ import annotations

import json
import traceback
from typing import Any

from sandbox_agent.sandbox.manager import ContainerDiedError, SandboxManager, current_thread_id
from sandbox_agent.sandbox.models import truncate_field
from sandbox_agent.settings import get_settings


def active_sessions_summary(
    manager: SandboxManager, *, filter_by_thread: bool = True
) -> list[dict[str, str]]:
    """Return a compact summary of active sessions.

    When *filter_by_thread* is ``True`` (default), only sessions visible
    to the current thread are returned.  When ``False``, all sessions are
    returned regardless of thread ownership.
    """
    thread_id = current_thread_id.get(None) if filter_by_thread else None
    return [
        {"session_id": info.session_id, "runtime": info.runtime, "status": info.status}
        for info in manager.sessions.values()
        if thread_id is None or info.thread_id is None or info.thread_id == thread_id
    ]


def error_payload(
    manager: SandboxManager, exc: Exception, *, filter_by_thread: bool = True
) -> dict[str, Any]:
    """Build a structured error payload that includes active sessions hint.

    Returns a ``dict`` suitable for both MCP (returned directly) and
    LangChain tools (serialised via ``error_response``).
    """
    sessions = active_sessions_summary(manager, filter_by_thread=filter_by_thread)
    settings = get_settings()

    if isinstance(exc, ContainerDiedError):
        return {
            "success": False,
            "error": f"CONTAINER_DIED: {exc.reason}",
            "session_id": exc.session_id,
            "hint": (
                "The sandbox container crashed and is no longer usable. "
                "Call stop_session to clean it up, then create_session to start a fresh one. "
                "BEFORE re-running code, diagnose the root cause: "
                "(1) Did you import_files to THIS session? Each session has its own "
                "filesystem — files imported to another session are NOT available here. "
                "(2) Did the code exhaust memory? "
                "(3) Did callback-based async code throw an unhandled error? "
                "NEVER re-run the exact same code that crashed a container."
            ),
            "active_sessions": sessions,
        }

    payload: dict[str, Any] = {
        "success": False,
        "error": f"{type(exc).__name__}: {exc}",
        "active_sessions": sessions,
    }

    if sessions:
        payload["hint"] = "Use one of the active session_ids listed above."
    else:
        payload["hint"] = "No active sessions. Call create_session first."

    if "not found" not in str(exc).lower():
        raw_tb = traceback.format_exc()
        payload["traceback"] = truncate_field(raw_tb, settings.MAX_TRACEBACK_CHARS)

    return payload


def error_response(manager: SandboxManager, exc: Exception) -> str:
    """Build a JSON-serialised error response (for LangChain tools)."""
    return json.dumps(error_payload(manager, exc), ensure_ascii=False)
