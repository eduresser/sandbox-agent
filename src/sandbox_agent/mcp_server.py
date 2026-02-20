"""MCP Server — exposes sandbox tools via Model Context Protocol.

Runs as a stdio server for integration with Cursor, Claude Desktop,
or any MCP-compatible client. Uses the same SandboxManager as the CLI.
"""

from __future__ import annotations

import base64
import tempfile
import traceback
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message=".*is not a Python type.*", module="pydantic")

from mcp.server.fastmcp import FastMCP

from sandbox_agent.sandbox.manager import ContainerDiedError, SandboxManager

mcp = FastMCP(
    "sandbox-agent",
    instructions=(
        "Sandboxed code execution in Docker containers. "
        "Create isolated Python/Node.js environments, execute code with "
        "persistent state, run terminal commands, and upload files."
        "Everything that can be answered by code execution should in some"
        "way can be answered by this agent."
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
        payload["traceback"] = traceback.format_exc()

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
        language: Sandbox language/runtime. Use "python" or "node".
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
    if info.stdout:
        result["stdout"] = info.stdout
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
        code: Code to execute (Python or Node.js, depending on the session runtime).
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
def upload_file(
    session_id: str,
    file_name: str,
    file_content: str,
    encoding: str = "text",
) -> dict[str, Any]:
    """Uploads a file into the sandbox's /workspace/ directory.

    Since MCP clients don't share a filesystem with the server, the file
    content is sent directly (as text or base64-encoded binary).

    Args:
        session_id: ID returned by create_session.
        file_name: Name for the file inside the sandbox (e.g. "data.csv").
        file_content: The file content as a string.
        encoding: "text" for plain text content (default), or "base64" for
            base64-encoded binary content.

    Returns:
        JSON with remote_path and size.
    """
    manager = _get_manager()
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file_name}") as tmp:
            if encoding == "base64":
                tmp.write(base64.b64decode(file_content))
            else:
                tmp.write(file_content.encode("utf-8"))
            tmp_path = Path(tmp.name)

        result = manager.upload_file(session_id, str(tmp_path), file_name)
    except Exception as exc:
        return _error_payload(manager, exc)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    return result


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
