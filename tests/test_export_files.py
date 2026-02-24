"""Tests for the export_files functionality.

Covers:
  - SandboxManager.export_files (manager layer)
  - LangChain export_files tool (tool layer)
  - MCP export_files tool (mcp layer)

Requires Docker to be running on the host.
Run with: pytest tests/test_export_files.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sandbox_agent.sandbox.manager import SandboxManager, current_thread_id
from sandbox_agent.sandbox.models import ExportFileResult, ExportResult
from sandbox_agent.tools import create_tools


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


@pytest.fixture()
def python_session(manager: SandboxManager):
    info = manager.create_session(runtime="python")
    yield info.session_id
    manager.stop_session(info.session_id)


def _create_file_in_sandbox(manager: SandboxManager, session_id: str, name: str, content: str):
    """Helper: create a file inside /workspace/ via execute_terminal."""
    escaped = content.replace("'", "'\\''")
    manager.execute_terminal(session_id, f"sh -c \"echo '{escaped}' > /workspace/{name}\"")


# ── Manager-level tests ───────────────────────────────────────


class TestExportFilesManager:
    def test_export_single_file(self, manager, python_session):
        _create_file_in_sandbox(manager, python_session, "hello.txt", "hello world")

        result = manager.export_files(
            python_session,
            [{"source": "hello.txt", "destination": "hello.txt"}],
        )

        assert isinstance(result, ExportResult)
        assert result.success is True
        assert len(result.files) == 1

        fr = result.files[0]
        assert isinstance(fr, ExportFileResult)
        assert fr.success is True
        assert fr.session_id == python_session
        assert fr.path == "/workspace/hello.txt"
        assert fr.size > 0

        # Verify file is in _exported_files and streamable
        # When current_thread_id is not set, manager uses session_id as thread_key
        thread_key = python_session
        assert manager.is_file_exported(thread_key, python_session, fr.path)
        content = b"".join(manager.stream_exported_file(None, python_session, fr.path))
        assert b"hello world" in content

    def test_export_multiple_files(self, manager, python_session):
        _create_file_in_sandbox(manager, python_session, "a.txt", "aaa")
        _create_file_in_sandbox(manager, python_session, "b.txt", "bbb")

        result = manager.export_files(
            python_session,
            [
                {"source": "a.txt", "destination": "a.txt"},
                {"source": "b.txt", "destination": "b.txt"},
            ],
        )

        assert result.success is True
        assert len(result.files) == 2
        assert all(f.success for f in result.files)
        paths = {f.path for f in result.files}
        assert "/workspace/a.txt" in paths
        assert "/workspace/b.txt" in paths

        thread_key = python_session
        assert manager.is_file_exported(thread_key, python_session, "/workspace/a.txt")
        assert manager.is_file_exported(thread_key, python_session, "/workspace/b.txt")

    def test_export_absolute_source(self, manager, python_session):
        _create_file_in_sandbox(manager, python_session, "abs.txt", "absolute")

        result = manager.export_files(
            python_session,
            [{"source": "/workspace/abs.txt", "destination": "abs.txt"}],
        )

        assert result.success is True
        assert result.files[0].path == "/workspace/abs.txt"
        content = b"".join(manager.stream_exported_file(None, python_session, "/workspace/abs.txt"))
        assert b"absolute" in content

    def test_export_nonexistent_file_partial_failure(self, manager, python_session):
        _create_file_in_sandbox(manager, python_session, "good.txt", "ok")

        result = manager.export_files(
            python_session,
            [
                {"source": "good.txt", "destination": "good.txt"},
                {"source": "nonexistent.xyz", "destination": "nope.xyz"},
            ],
        )

        assert result.success is False
        assert result.files[0].success is True
        assert result.files[1].success is False
        assert result.files[1].error

    def test_export_empty_source_error(self, manager, python_session):
        result = manager.export_files(
            python_session,
            [{"source": "", "destination": "x.txt"}],
        )

        assert result.success is False
        assert result.files[0].success is False
        assert "source is required" in result.files[0].error

    def test_export_directory(self, manager, python_session):
        manager.execute_terminal(python_session, "mkdir -p /workspace/mydir")
        _create_file_in_sandbox(manager, python_session, "mydir/f1.txt", "file1")
        _create_file_in_sandbox(manager, python_session, "mydir/f2.txt", "file2")

        result = manager.export_files(
            python_session,
            [{"source": "mydir", "destination": "mydir"}],
        )

        assert result.success is True
        assert result.files[0].path == "/workspace/mydir"
        content = b"".join(manager.stream_exported_file(None, python_session, "/workspace/mydir"))
        assert content.startswith(b"PK"), "Expected ZIP format"
        assert b"mydir/f1.txt" in content
        assert b"mydir/f2.txt" in content

    def test_export_path_traversal_rejected(self, manager, python_session):
        result = manager.export_files(
            python_session,
            [{"source": "/workspace/../etc/passwd", "destination": "x"}],
        )
        assert result.success is False
        assert "outside /workspace" in result.files[0].error

    def test_export_invalid_session(self, manager):
        with pytest.raises(ValueError, match="not found"):
            manager.export_files(
                "nonexistent_id",
                [{"source": "x", "destination": "x"}],
            )

    def test_stop_session_removes_exported_files(self, manager):
        """stop_session removes session from _exported_files."""
        info = manager.create_session(runtime="python")
        sid = info.session_id
        _create_file_in_sandbox(manager, sid, "cleanup_test.txt", "to be removed")

        manager.export_files(sid, [{"source": "cleanup_test.txt"}])

        thread_key = sid
        assert manager.is_file_exported(thread_key, sid, "/workspace/cleanup_test.txt")

        manager.stop_session(sid)

        assert not manager.is_file_exported(thread_key, sid, "/workspace/cleanup_test.txt")

    def test_cleanup_thread_sessions_removes_exported_files(self, manager):
        """cleanup_thread_sessions removes _exported_files for the thread."""
        settings = __import__("sandbox_agent.settings", fromlist=["get_settings"]).get_settings()
        token = current_thread_id.set("test_thread_cleanup")
        try:
            info = manager.create_session(runtime="python")
            sid = info.session_id
            _create_file_in_sandbox(manager, sid, "thread_cleanup.txt", "to be removed")
            manager.export_files(sid, [{"source": "thread_cleanup.txt"}])

            assert manager.is_file_exported("test_thread_cleanup", sid, "/workspace/thread_cleanup.txt")

            manager.cleanup_thread_sessions("test_thread_cleanup")

            assert not manager.is_file_exported("test_thread_cleanup", sid, "/workspace/thread_cleanup.txt")
        finally:
            current_thread_id.reset(token)


# ── LangChain tool-level tests ────────────────────────────────


class TestExportFilesTool:
    @pytest.fixture(scope="class")
    def class_manager(self):
        mgr = SandboxManager()
        yield mgr
        mgr.cleanup_all()

    @pytest.fixture(scope="class")
    def tools(self, class_manager):
        return {t.name: t for t in create_tools(class_manager)}

    def test_tool_exists(self, tools):
        assert "export_files" in tools
        assert tools["export_files"].description

    def test_tool_full_workflow(self, tools, class_manager):
        create = tools["create_session"]
        execute_terminal = tools["execute_terminal"]
        export = tools["export_files"]
        stop = tools["stop_session"]

        r = json.loads(create.invoke({"language": "python"}))
        sid = r["session_id"]

        try:
            json.loads(execute_terminal.invoke({
                "session_id": sid,
                "command": "echo 'tool_export_test' > /workspace/tool_file.txt",
            }))

            r = json.loads(export.invoke({
                "session_id": sid,
                "files": [{"source": "tool_file.txt", "destination": "tool_file.txt"}],
            }))

            assert r["success"] is True
            assert len(r["files"]) == 1
            assert r["files"][0]["success"] is True
            assert r["files"][0]["session_id"] == sid
            assert "path" in r["files"][0]
            assert "tool_file.txt" in r["files"][0]["path"]
        finally:
            json.loads(stop.invoke({"session_id": sid}))


# ── MCP tool-level tests ──────────────────────────────────────


class TestExportFilesMCP:
    def test_mcp_export(self, manager, python_session, monkeypatch):
        """Test the MCP export_files function directly (not via MCP transport)."""
        _create_file_in_sandbox(manager, python_session, "mcp_test.txt", "mcp_data")

        from sandbox_agent import mcp_server

        monkeypatch.setattr(mcp_server, "get_manager", lambda: manager)

        result = mcp_server.export_files(
            session_id=python_session,
            files=[{"source": "mcp_test.txt", "destination": "mcp_test.txt"}],
        )

        assert result["success"] is True
        assert len(result["files"]) == 1
        assert result["files"][0]["success"] is True
        assert result["files"][0]["session_id"] == python_session
        assert result["files"][0]["path"] == "/workspace/mcp_test.txt"

        # MCP sets current_thread_id, so export uses mcp thread_id; stream accepts thread_id or session_id
        tid = mcp_server._thread_id()
        content = b"".join(manager.stream_exported_file(tid, python_session, "/workspace/mcp_test.txt"))
        assert b"mcp_data" in content

    def test_mcp_export_returns_session_id_and_path(self, manager, python_session, monkeypatch):
        """Verify the output includes session_id and path for cross-session transfer."""
        _create_file_in_sandbox(manager, python_session, "transfer.csv", "1,2,3")

        from sandbox_agent import mcp_server

        monkeypatch.setattr(mcp_server, "get_manager", lambda: manager)

        result = mcp_server.export_files(
            session_id=python_session,
            files=[{"source": "transfer.csv", "destination": "transfer.csv"}],
        )

        file_entry = result["files"][0]
        assert "session_id" in file_entry
        assert "path" in file_entry
        assert file_entry["session_id"] == python_session
        assert "/workspace/transfer.csv" in file_entry["path"]
