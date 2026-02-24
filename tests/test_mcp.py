"""Tests for all MCP server tools.

Covers the 6 MCP tools by calling them directly (no stdio transport),
with monkeypatched get_manager to inject a shared SandboxManager.

Requires Docker to be running on the host.
Run with: pytest tests/test_mcp.py -v
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sandbox_agent.sandbox.manager import SandboxManager


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker is not available",
)


@pytest.fixture(scope="module")
def manager():
    mgr = SandboxManager()
    yield mgr
    mgr.cleanup_all()


@pytest.fixture(autouse=True)
def _patch_manager(manager, monkeypatch):
    """Redirect mcp_server.get_manager to our shared instance."""
    from sandbox_agent import mcp_server

    monkeypatch.setattr(mcp_server, "get_manager", lambda: manager)


@pytest.fixture()
def python_session(manager: SandboxManager):
    info = manager.create_session(runtime="python")
    yield info.session_id
    manager.stop_session(info.session_id)


# ── create_session ─────────────────────────────────────────────


class TestMCPCreateSession:
    def test_create_python(self):
        from sandbox_agent.mcp_server import create_session, stop_session

        result = create_session(language="python")
        assert result["success"] is True
        assert "session_id" in result
        assert result["runtime"] == "python"
        stop_session(session_id=result["session_id"])

    def test_create_node(self):
        from sandbox_agent.mcp_server import create_session, stop_session

        result = create_session(language="node")
        assert result["success"] is True
        assert result["runtime"] == "node"
        stop_session(session_id=result["session_id"])

    def test_create_with_deps(self):
        from sandbox_agent.mcp_server import create_session, stop_session

        result = create_session(language="python", dependencies={"requests": ""})
        assert result["success"] is True
        assert result["dependencies"] == {"requests": ""}
        stop_session(session_id=result["session_id"])

    def test_invalid_runtime_returns_error(self):
        from sandbox_agent.mcp_server import create_session

        result = create_session(language="brainfuck")
        assert result["success"] is False
        assert "error" in result


# ── execute_code ───────────────────────────────────────────────


class TestMCPExecuteCode:
    def test_simple_expression(self, python_session):
        from sandbox_agent.mcp_server import execute_code

        result = execute_code(session_id=python_session, code="2 + 3")
        assert result["success"] is True
        assert result["result"] is not None

    def test_stdout_capture(self, python_session):
        from sandbox_agent.mcp_server import execute_code

        result = execute_code(session_id=python_session, code="print('hello mcp')")
        assert result["success"] is True
        assert "hello mcp" in result["stdout"]

    def test_error_returns_error_field(self, python_session):
        from sandbox_agent.mcp_server import execute_code

        result = execute_code(session_id=python_session, code="1/0")
        assert result["success"] is False
        assert result["error"] is not None

    def test_invalid_session_returns_error(self):
        from sandbox_agent.mcp_server import execute_code

        result = execute_code(session_id="nonexistent", code="1+1")
        assert result["success"] is False
        assert "error" in result


# ── execute_terminal ───────────────────────────────────────────


class TestMCPExecuteTerminal:
    def test_ls(self, python_session):
        from sandbox_agent.mcp_server import execute_terminal

        result = execute_terminal(session_id=python_session, command="ls /workspace")
        assert result["exit_code"] == 0

    def test_echo(self, python_session):
        from sandbox_agent.mcp_server import execute_terminal

        result = execute_terminal(session_id=python_session, command="echo 'mcp_term'")
        assert result["exit_code"] == 0
        assert "mcp_term" in result["stdout"]

    def test_failed_command(self, python_session):
        from sandbox_agent.mcp_server import execute_terminal

        result = execute_terminal(session_id=python_session, command="false")
        assert result["exit_code"] != 0

    def test_invalid_session_returns_error(self):
        from sandbox_agent.mcp_server import execute_terminal

        result = execute_terminal(session_id="nonexistent", command="ls")
        assert result["success"] is False
        assert "error" in result


# ── import_files ───────────────────────────────────────────────


class TestMCPImportFiles:
    def test_host_path(self, python_session):
        """Import from host path (source + destination)."""
        from sandbox_agent.mcp_server import execute_terminal, import_files

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("host file content")
            host_path = f.name

        try:
            result = import_files(
                session_id=python_session,
                files=[{"source": host_path, "destination": "hello.txt"}],
            )
            assert result["success"] is True
            assert len(result["files"]) == 1
            assert result["files"][0]["success"] is True

            cat = execute_terminal(session_id=python_session, command="cat /workspace/hello.txt")
            assert "host file content" in cat["stdout"]
        finally:
            Path(host_path).unlink(missing_ok=True)

    def test_cross_session(self, manager):
        """Cross-session import requires a shared thread context."""
        from sandbox_agent.sandbox.manager import current_thread_id

        from sandbox_agent.mcp_server import (
            create_session,
            execute_terminal,
            export_files,
            import_files,
            stop_session,
        )

        token = current_thread_id.set("mcp_cross_session_test")
        src_id = dst_id = None
        try:
            src = create_session(language="python")
            dst = create_session(language="python")
            src_id, dst_id = src["session_id"], dst["session_id"]

            execute_terminal(
                session_id=src_id,
                command="echo 'cross_session_data' > /workspace/transfer.txt",
            )
            export_files(
                session_id=src_id,
                files=[{"source": "transfer.txt"}],
            )

            result = import_files(
                session_id=dst_id,
                files=[{
                    "session_id": src_id,
                    "path": "/workspace/transfer.txt",
                    "destination": "received.txt",
                }],
            )
            assert result["success"] is True, f"Import failed: {result}"

            cat = execute_terminal(session_id=dst_id, command="cat /workspace/received.txt")
            assert "cross_session_data" in cat["stdout"]
        finally:
            current_thread_id.reset(token)
            if src_id:
                stop_session(session_id=src_id)
            if dst_id:
                stop_session(session_id=dst_id)

    def test_invalid_session_returns_error(self):
        from sandbox_agent.mcp_server import import_files

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("data")
            host_path = f.name

        try:
            result = import_files(
                session_id="nonexistent",
                files=[{"source": host_path, "destination": "x.txt"}],
            )
            assert result["success"] is False
            assert "error" in result
        finally:
            Path(host_path).unlink(missing_ok=True)


# ── stop_session ───────────────────────────────────────────────


class TestMCPStopSession:
    def test_stop_existing(self, manager):
        from sandbox_agent.mcp_server import create_session, stop_session

        r = create_session(language="python")
        sid = r["session_id"]

        result = stop_session(session_id=sid)
        assert result["success"] is True
        assert sid not in manager.sessions

    def test_stop_nonexistent_returns_false(self):
        from sandbox_agent.mcp_server import stop_session

        result = stop_session(session_id="nonexistent")
        assert result["success"] is False


# ── error handling ─────────────────────────────────────────────


class TestMCPErrorHandling:
    def test_error_payload_structure(self):
        from sandbox_agent.mcp_server import execute_code

        result = execute_code(session_id="does_not_exist", code="1")
        assert result["success"] is False
        assert "error" in result
        assert "active_sessions" in result
        assert isinstance(result["active_sessions"], list)
        assert "hint" in result


# ── full workflow ──────────────────────────────────────────────


class TestMCPWorkflow:
    def test_full_lifecycle(self):
        from sandbox_agent.mcp_server import (
            create_session,
            execute_code,
            execute_terminal,
            export_files,
            stop_session,
        )

        r = create_session(language="python")
        assert r["success"] is True
        sid = r["session_id"]

        try:
            r = execute_code(session_id=sid, code="x = 42\nprint(x)")
            assert r["success"] is True
            assert "42" in r["stdout"]

            r = execute_code(session_id=sid, code="x * 2")
            assert r["success"] is True

            r = execute_terminal(
                session_id=sid,
                command="echo 'workflow_test' > /workspace/out.txt",
            )
            assert r["exit_code"] == 0

            r = export_files(
                session_id=sid,
                files=[{"source": "out.txt"}],
            )
            assert r["success"] is True
            assert r["files"][0]["path"] == "/workspace/out.txt"

            r = stop_session(session_id=sid)
            assert r["success"] is True
        except Exception:
            stop_session(session_id=sid)
            raise
