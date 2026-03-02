"""Unified core tool functions shared by LangChain tools and MCP server.

Each function takes a ``SandboxManager`` plus tool arguments and returns a
plain ``dict``.  LangChain wrappers serialise the result to JSON; MCP
wrappers return it directly.

All inputs are validated via Pydantic models (see ``_schemas.py``).  When
validation fails the function returns a structured error dict instead of
raising, so the LLM can self-correct.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from sandbox_agent.sandbox.manager import SandboxManager, current_thread_id
from sandbox_agent.settings import get_settings
from sandbox_agent.tools._helpers import error_payload, validation_error_payload
from sandbox_agent.tools._schemas import (
    CreateSessionInput,
    ExecuteCodeInput,
    ExecuteTerminalInput,
    ExportFilesInput,
    ImportFilesInput,
    StopSessionInput,
)

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
        params = CreateSessionInput(language=language, dependencies=dependencies)
    except ValidationError as exc:
        return validation_error_payload(exc)

    try:
        info = manager.create_session(
            runtime=params.language,
            dependencies=params.dependencies,
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
        params = ExecuteCodeInput(session_id=session_id, code=code, timeout=timeout)
    except ValidationError as exc:
        return validation_error_payload(exc)

    try:
        result = manager.execute_code(
            params.session_id, params.code, timeout=params.timeout or 30
        )
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
        params = ExecuteTerminalInput(session_id=session_id, command=command)
    except ValidationError as exc:
        return validation_error_payload(exc)

    try:
        result = manager.execute_terminal(params.session_id, params.command)
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
        params = ImportFilesInput(session_id=session_id, files=files)
    except ValidationError as exc:
        return validation_error_payload(exc)

    try:
        result = manager.import_files(
            params.session_id, [f.model_dump(exclude_none=True) for f in params.files]
        )
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
    try:
        params = ExportFilesInput(session_id=session_id, files=files)
    except ValidationError as exc:
        return validation_error_payload(exc)

    if thread_id is None:
        thread_id = current_thread_id.get(None)

    try:
        result = manager.export_files(
            params.session_id, [f.model_dump(exclude_none=True) for f in params.files]
        )
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
        params = StopSessionInput(session_id=session_id)
    except ValidationError as exc:
        return validation_error_payload(exc)

    try:
        success = manager.stop_session(params.session_id)
    except Exception as exc:
        return error_payload(manager, exc, filter_by_thread=filter_by_thread)

    return {"success": success}
