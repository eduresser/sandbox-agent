"""Unified core tool functions shared by LangChain tools and MCP server.

Each function takes a ``SandboxManager`` plus tool arguments and returns a
plain ``dict``.  LangChain wrappers serialise the result to JSON; MCP
wrappers return it directly.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from urllib.parse import quote

from sandbox_agent.sandbox.manager import SandboxManager, current_thread_id
from sandbox_agent.settings import get_settings
from sandbox_agent.tools._helpers import error_payload

# ── Helpers ────────────────────────────────────────────────────────────────


def _download_url(session_id: str, path: str, *, thread_id: str) -> str:
    base_url = get_settings().API_BASE_URL.rstrip("/")
    path_enc = quote(path, safe="")
    return (
        f"{base_url}/threads/{thread_id}/files/download"
        f"?session_id={session_id}&path={path_enc}"
    )


def _enrich_export_result(result_dict: dict, *, thread_id: str | None) -> None:
    for f in result_dict.get("files", []):
        if f.get("success") and thread_id:
            f["download_url"] = _download_url(
                f.get("session_id", ""), f.get("path", ""), thread_id=thread_id
            )
        else:
            f["download_url"] = None


# ── Core tool functions ────────────────────────────────────────────────────


def create_session(
    manager: SandboxManager,
    language: str = "python",
    dependencies: dict[str, str] = {},
    *,
    filter_by_thread: bool = True,
) -> dict[str, Any]:
    try:
        dependencies = {
            k: "" if v is None else str(v)
            for k, v in dependencies.items()
        }
        info = manager.create_session(
            runtime=language or "python",
            dependencies=dependencies,
        )
    except Exception as exc:
        return error_payload(manager, exc, filter_by_thread=filter_by_thread)

    result: dict[str, Any] = {
        "success": True,
        "session_id": info.session_id,
        "runtime": info.runtime,
        "status": info.status,
        "dependencies": info.dependencies,
    }
    if info.stderr:
        result["stderr"] = info.stderr
    return result


def execute_code(
    manager: SandboxManager,
    session_id: str,
    code: str,
    timeout: int | None = None,
    *,
    filter_by_thread: bool = True,
) -> dict[str, Any]:
    try:
        result = manager.execute_code(session_id, code, timeout=timeout or 30)
    except Exception as exc:
        return error_payload(manager, exc, filter_by_thread=filter_by_thread)

    return {
        "success": result.success,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "result": result.result,
        "error": result.error,
        "figures": result.figures,
    }


def execute_terminal(
    manager: SandboxManager,
    session_id: str,
    command: str,
    *,
    filter_by_thread: bool = True,
) -> dict[str, Any]:
    try:
        result = manager.execute_terminal(session_id, command)
    except Exception as exc:
        return error_payload(manager, exc, filter_by_thread=filter_by_thread)

    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def import_files(
    manager: SandboxManager,
    session_id: str,
    files: list[dict[str, str]],
    *,
    filter_by_thread: bool = True,
) -> dict[str, Any]:
    try:
        result = manager.import_files(session_id, list(files))
    except Exception as exc:
        return error_payload(manager, exc, filter_by_thread=filter_by_thread)

    return asdict(result)


def export_files(
    manager: SandboxManager,
    session_id: str,
    files: list[dict[str, str]],
    *,
    thread_id: str | None = None,
    filter_by_thread: bool = True,
) -> dict[str, Any]:
    """Export files and enrich with download URLs.

    All callers (UI, CLI, MCP) use the same endpoint format:
    ``/threads/{thread_id}/files/download``.

    When *thread_id* is not provided explicitly, falls back to
    ``current_thread_id`` context var (set by the agent graph or MCP server).
    """
    if thread_id is None:
        thread_id = current_thread_id.get(None)

    try:
        result = manager.export_files(session_id, list(files))
    except Exception as exc:
        return error_payload(manager, exc, filter_by_thread=filter_by_thread)

    result_dict = asdict(result)
    _enrich_export_result(result_dict, thread_id=thread_id)
    return result_dict


def stop_session(
    manager: SandboxManager,
    session_id: str,
    *,
    filter_by_thread: bool = True,
) -> dict[str, Any]:
    try:
        success = manager.stop_session(session_id)
    except Exception as exc:
        return error_payload(manager, exc, filter_by_thread=filter_by_thread)

    return {"success": success}
