"""MCP Server — exposes sandbox tools via Model Context Protocol.

Runs as a stdio server for integration with Cursor, Claude Desktop,
or any MCP-compatible client. Uses the same SandboxManager as the CLI.
"""

from __future__ import annotations

import base64
import tempfile
import traceback
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from sandbox_agent.sandbox.manager import ContainerDiedError, SandboxManager
from sandbox_agent.sandbox.models import truncate_field
from sandbox_agent.settings import get_settings

mcp = FastMCP(
    "sandbox-agent",
    instructions=(
        "Sandboxed code execution in Docker containers. "
        "Create isolated Python/Node.js/R/Julia environments, execute code with "
        "persistent state, run terminal commands, and import/export files. "
        "Everything that can be answered by code execution should in some "
        "way be answered by this agent."
    ),
)

_manager: SandboxManager | None = None


def _get_manager() -> SandboxManager:
    global _manager
    if _manager is None:
        _manager = SandboxManager()
    return _manager


def _active_sessions(manager: SandboxManager) -> list[dict[str, str]]:
    return [
        {"session_id": info.session_id, "runtime": info.runtime, "status": info.status}
        for info in manager.sessions.values()
    ]


def _error_payload(manager: SandboxManager, exc: Exception) -> dict[str, Any]:
    sessions = _active_sessions(manager)

    if isinstance(exc, ContainerDiedError):
        return {
            "success": False,
            "error": f"CONTAINER_DIED: {exc.reason}",
            "session_id": exc.session_id,
            "hint": (
                "The sandbox container crashed. "
                "Call stop_session to clean it up, then create_session for a fresh one."
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
        payload["traceback"] = truncate_field(raw_tb, get_settings().MAX_TRACEBACK_CHARS)

    return payload


# ── Tools ─────────────────────────────────────────────


@mcp.tool()
def create_session(
    language: str = "python",
    dependencies: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Creates an isolated sandbox environment (Docker container).

    The sandbox starts empty — specify ALL needed packages in dependencies.
    For data analysis, always include pandas and numpy.

    Args:
        language: Sandbox language/runtime. Use "python", "node", "r", or "julia".
        dependencies: Packages to pre-install. Keys are package names,
            values are versions (use "" for latest).
            Example: {"pandas": "", "numpy": "", "matplotlib": "3.9.0"}.

    Returns:
        JSON with session_id and session info.
    """
    manager = _get_manager()
    try:
        info = manager.create_session(
            runtime=language or "python",
            dependencies=dict(dependencies or {}),
        )
    except Exception as exc:
        return _error_payload(manager, exc)

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


@mcp.tool()
def execute_code(
    session_id: str,
    code: str,
    timeout: int = 30,
) -> dict[str, Any]:
    """Executes code in the sandbox. State persists across calls (like Jupyter cells).

    Args:
        session_id: ID returned by create_session.
        code: Code to execute (Python, Node.js, R, or Julia, depending on the session runtime).
        timeout: Max execution time in seconds (max 300). Defaults to 30.

    Returns:
        JSON with success, stdout, stderr, result, error, and figures.
    """
    manager = _get_manager()
    try:
        result = manager.execute_code(session_id, code, timeout=timeout or 30)
    except Exception as exc:
        return _error_payload(manager, exc)

    return {
        "success": result.success,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "result": result.result,
        "error": result.error,
        "figures": result.figures,
    }


@mcp.tool()
def execute_terminal(session_id: str, command: str) -> dict[str, Any]:
    """Runs a shell command inside the sandbox.

    Useful for listing files, installing Linux dependencies, etc.

    Args:
        session_id: ID returned by create_session.
        command: Shell command to execute (e.g. "ls -la", "apt-get install -y curl").

    Returns:
        JSON with stdout, stderr, and exit_code.
    """
    manager = _get_manager()
    try:
        result = manager.execute_terminal(session_id, command)
    except Exception as exc:
        return _error_payload(manager, exc)

    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


@mcp.tool()
def import_files(
    session_id: str,
    files: list[dict[str, str]],
) -> dict[str, Any]:
    """Imports files into the sandbox from host, inline content, or another session.

    Each entry can be:
    - Inline: "file_name", "file_content", optional "encoding" (text/base64)
    - Host path: "source" (host path), optional "destination"
    - Cross-session: "session_id" (source session), "path" (container path),
      optional "destination" — file must have been exported from that session.

    Args:
        session_id: ID returned by create_session (destination).
        files: List of file objects. Examples:
            [{"file_name": "data.csv", "file_content": "a,b\\n1,2"}]
            [{"source": "/tmp/report.pdf", "destination": "report.pdf"}]
            [{"session_id": "abc123", "path": "/workspace/out.csv", "destination": "out.csv"}]

    Returns:
        JSON with per-file results (source, destination, success, size, error).
    """
    manager = _get_manager()
    tmp_paths: list[Path] = []
    resolved_files: list[dict[str, str]] = []

    try:
        for entry in files:
            if "file_content" in entry:
                fname = entry.get("file_name", "file")
                encoding = entry.get("encoding", "text")
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=f"_{fname}"
                ) as tmp:
                    if encoding == "base64":
                        tmp.write(base64.b64decode(entry["file_content"]))
                    else:
                        tmp.write(entry["file_content"].encode("utf-8"))
                    tmp_paths.append(Path(tmp.name))
                resolved_files.append({
                    "source": str(tmp_paths[-1]),
                    "destination": fname,
                })
            elif entry.get("session_id"):
                resolved_files.append({
                    "session_id": entry["session_id"],
                    "path": entry.get("path", ""),
                    "destination": entry.get("destination", ""),
                })
            else:
                resolved_files.append({
                    "source": entry.get("source", ""),
                    "destination": entry.get("destination", ""),
                })

        from dataclasses import asdict

        result = manager.import_files(session_id, resolved_files)
    except Exception as exc:
        return _error_payload(manager, exc)
    finally:
        for p in tmp_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    return asdict(result)


@mcp.tool()
def export_files(
    session_id: str,
    files: list[dict[str, str]],
) -> dict[str, Any]:
    """Registers files for download and cross-session import (no host copy).

    Files become available via GET /files/download?session_id=...&path=...
    (MCP has no thread_id). Use import_files with session_id+path for cross-session.

    Args:
        session_id: ID returned by create_session.
        files: List of objects with "source" (path in container).
            Example: [{"source": "report.pdf"}, {"source": "/workspace/data.csv"}]

    Returns:
        JSON with per-file results (session_id, path, success, size, error).
        path is always absolute (e.g. /workspace/file.png).
    """
    manager = _get_manager()
    try:
        from dataclasses import asdict

        result = manager.export_files(session_id, list(files))
    except Exception as exc:
        return _error_payload(manager, exc)

    return asdict(result)


@mcp.tool()
def stop_session(session_id: str) -> dict[str, Any]:
    """Stops and removes the sandbox completely. Use when done with a session.

    Args:
        session_id: ID returned by create_session.

    Returns:
        JSON with success status.
    """
    manager = _get_manager()
    try:
        success = manager.stop_session(session_id)
    except Exception as exc:
        return _error_payload(manager, exc)

    return {"success": success}


# ── Entrypoint ────────────────────────────────────────


def main() -> None:
    """Run the MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
