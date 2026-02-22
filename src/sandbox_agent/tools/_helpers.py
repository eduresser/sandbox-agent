"""Shared helpers for tool error responses."""

from __future__ import annotations

import json
import traceback

from sandbox_agent.sandbox.manager import ContainerDiedError, SandboxManager


def active_sessions_summary(manager: SandboxManager) -> list[dict]:
    """Return a compact summary of all active sessions."""
    return [
        {"session_id": info.session_id, "runtime": info.runtime, "status": info.status}
        for info in manager.sessions.values()
    ]


def error_response(manager: SandboxManager, exc: Exception) -> str:
    """Build a JSON error response that includes active sessions hint."""
    sessions = active_sessions_summary(manager)

    if isinstance(exc, ContainerDiedError):
        payload: dict = {
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
        return json.dumps(payload, ensure_ascii=False)

    payload = {
        "success": False,
        "error": f"{type(exc).__name__}: {exc}",
    }

    if sessions:
        payload["active_sessions"] = sessions
        payload["hint"] = "Use one of the active session_ids listed above."
    else:
        payload["active_sessions"] = []
        payload["hint"] = "No active sessions. Call create_session first."

    if "not found" not in str(exc).lower():
        payload["traceback"] = traceback.format_exc()

    return json.dumps(payload, ensure_ascii=False)
