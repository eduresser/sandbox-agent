"""MCP Server — exposes sandbox tools via Model Context Protocol.

Runs as a stdio server for integration with Cursor, Claude Desktop,
or any MCP-compatible client.  Uses the same core tool functions as
the LangGraph agent, via :mod:`sandbox_agent.tools._core`.

Like the CLI, the MCP server maintains a persistent thread_id so that
export download URLs use the standard ``/threads/{id}/files/download``
endpoint — the same one used by the UI and CLI.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

# Ensure CWD is the project root so pydantic_settings can find .env
# (Cursor may start the MCP process with a different working directory)
_project_root = Path(__file__).resolve().parent.parent.parent
if _project_root.is_dir() and (_project_root / ".env").exists():
    os.chdir(_project_root)

from mcp.server.fastmcp import FastMCP

from sandbox_agent.sandbox import get_manager
from sandbox_agent.sandbox.manager import current_thread_id
from sandbox_agent.tools import _core

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

# ── Persistent MCP thread ──────────────────────────────

_STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
) / "sandbox-agent"


def _get_mcp_thread_id() -> str:
    """Load or create a persistent thread_id for MCP sessions."""
    path = _STATE_DIR / "mcp-thread.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))["thread_id"]
        except Exception:
            pass
    tid = str(uuid.uuid4())
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"thread_id": tid}), encoding="utf-8")
    return tid


_mcp_thread_id: str | None = None


def _thread_id() -> str:
    global _mcp_thread_id
    if _mcp_thread_id is None:
        _mcp_thread_id = _get_mcp_thread_id()
    return _mcp_thread_id


def _set_thread() -> None:
    """Set the current_thread_id context var so the manager tracks this MCP session."""
    current_thread_id.set(_thread_id())


# ── Tools ─────────────────────────────────────────────


@mcp.tool()
def create_session(
    language: str = "python",
    dependencies: dict[str, str] = {},
) -> dict[str, Any]:
    """Creates an isolated sandbox environment (Docker container).

    The sandbox starts empty — specify ALL needed packages in dependencies.
    For data analysis, always include pandas and numpy.

    Args:
        language: Sandbox language/runtime. Use "python", "node", "r", or "julia".
        dependencies: Packages to pre-install. Keys are package names,
            values are version strings (use "" for latest). Always use strings,
            never numbers — e.g. "2.2" not 2.2. Null/None is treated as "".
            Example: {"pandas": "", "numpy": "1.26", "matplotlib": "3.9.0"}.

    Returns:
        JSON with session_id and session info.
    """
    _set_thread()
    return _core.create_session(get_manager(), language, dependencies)


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
    _set_thread()
    return _core.execute_code(get_manager(), session_id, code, timeout)


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
    _set_thread()
    return _core.execute_terminal(get_manager(), session_id, command)


@mcp.tool()
def import_files(
    session_id: str,
    files: list[dict[str, str]],
) -> dict[str, Any]:
    """Imports files into the sandbox from host or from another session.

    Each entry can be:
    - Host path: "source" (host path), optional "destination"
    - Cross-session: "session_id" (source session), "path" (container path),
      optional "destination" — file must have been exported from that session.

    Args:
        session_id: ID returned by create_session (destination).
        files: List of file objects. Examples:
            [{"source": "/tmp/report.pdf", "destination": "report.pdf"}]
            [{"session_id": "abc123", "path": "/workspace/out.csv", "destination": "out.csv"}]

    Returns:
        JSON with per-file results (source, destination, success, size, error).
    """
    _set_thread()
    return _core.import_files(get_manager(), session_id, list(files))


@mcp.tool()
def export_files(
    session_id: str,
    files: list[dict[str, str]],
) -> dict[str, Any]:
    """Registers files for download and cross-session import (no host copy).

    Files become available via the API download endpoint and for import_files
    in other sessions. Result includes download_url for each file (API must
    be running).

    Args:
        session_id: ID returned by create_session.
        files: List of objects with "source" (path in container).
            Example: [{"source": "report.pdf"}, {"source": "/workspace/data.csv"}]

    Returns:
        JSON with per-file results (session_id, path, success, size, error,
        download_url). path is always absolute (e.g. /workspace/file.png).
    """
    _set_thread()
    return _core.export_files(get_manager(), session_id, list(files))


@mcp.tool()
def stop_session(session_id: str) -> dict[str, Any]:
    """Stops and removes the sandbox completely. Use when done with a session.

    Args:
        session_id: ID returned by create_session.

    Returns:
        JSON with success status.
    """
    _set_thread()
    return _core.stop_session(get_manager(), session_id)


# ── Entrypoint ────────────────────────────────────────


def main() -> None:
    """Run the MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
