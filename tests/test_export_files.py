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
import shutil
import tempfile
from pathlib import Path

import pytest

from sandbox_agent.sandbox.manager import SandboxManager
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


@pytest.fixture()
def output_dir():
    d = Path(tempfile.mkdtemp(prefix="sandbox_export_test_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _create_file_in_sandbox(manager: SandboxManager, session_id: str, name: str, content: str):
    """Helper: create a file inside /workspace/ via execute_terminal."""
    escaped = content.replace("'", "'\\''")
    manager.execute_terminal(session_id, f"sh -c \"echo '{escaped}' > /workspace/{name}\"")


# ── Manager-level tests ───────────────────────────────────────


class TestExportFilesManager:
    def test_export_single_file(self, manager, python_session, output_dir):
        _create_file_in_sandbox(manager, python_session, "hello.txt", "hello world")

        result = manager.export_files(
            python_session,
            [{"source": "hello.txt", "destination": "hello.txt"}],
            output_dir=str(output_dir),
        )

        assert isinstance(result, ExportResult)
        assert result.success is True
        assert len(result.files) == 1

        fr = result.files[0]
        assert isinstance(fr, ExportFileResult)
        assert fr.success is True
        assert fr.size > 0
        assert Path(fr.destination).exists()
        assert "hello world" in Path(fr.destination).read_text()

    def test_export_multiple_files(self, manager, python_session, output_dir):
        _create_file_in_sandbox(manager, python_session, "a.txt", "aaa")
        _create_file_in_sandbox(manager, python_session, "b.txt", "bbb")

        result = manager.export_files(
            python_session,
            [
                {"source": "a.txt", "destination": "a.txt"},
                {"source": "b.txt", "destination": "b.txt"},
            ],
            output_dir=str(output_dir),
        )

        assert result.success is True
        assert len(result.files) == 2
        assert all(f.success for f in result.files)

        session_dir = output_dir / python_session
        assert (session_dir / "a.txt").exists()
        assert (session_dir / "b.txt").exists()

    def test_export_with_subdirectory_destination(self, manager, python_session, output_dir):
        _create_file_in_sandbox(manager, python_session, "data.csv", "a,b,c")

        result = manager.export_files(
            python_session,
            [{"source": "data.csv", "destination": "subdir/data.csv"}],
            output_dir=str(output_dir),
        )

        assert result.success is True
        exported = Path(result.files[0].destination)
        assert exported.exists()
        assert "a,b,c" in exported.read_text()

    def test_export_absolute_source(self, manager, python_session, output_dir):
        _create_file_in_sandbox(manager, python_session, "abs.txt", "absolute")

        result = manager.export_files(
            python_session,
            [{"source": "/workspace/abs.txt", "destination": "abs.txt"}],
            output_dir=str(output_dir),
        )

        assert result.success is True
        assert "absolute" in Path(result.files[0].destination).read_text()

    def test_export_absolute_destination(self, manager, python_session, output_dir):
        _create_file_in_sandbox(manager, python_session, "out.txt", "dest_abs")

        abs_dest = str(output_dir / "custom" / "out.txt")
        result = manager.export_files(
            python_session,
            [{"source": "out.txt", "destination": abs_dest}],
            output_dir=str(output_dir),
        )

        assert result.success is True
        assert Path(abs_dest).exists()
        assert "dest_abs" in Path(abs_dest).read_text()

    def test_export_default_destination_uses_filename(self, manager, python_session, output_dir):
        _create_file_in_sandbox(manager, python_session, "auto.txt", "auto")

        result = manager.export_files(
            python_session,
            [{"source": "auto.txt", "destination": ""}],
            output_dir=str(output_dir),
        )

        assert result.success is True
        assert (output_dir / python_session / "auto.txt").exists()

    def test_export_nonexistent_file_partial_failure(self, manager, python_session, output_dir):
        _create_file_in_sandbox(manager, python_session, "good.txt", "ok")

        result = manager.export_files(
            python_session,
            [
                {"source": "good.txt", "destination": "good.txt"},
                {"source": "nonexistent.xyz", "destination": "nope.xyz"},
            ],
            output_dir=str(output_dir),
        )

        assert result.success is False
        assert result.files[0].success is True
        assert result.files[1].success is False
        assert result.files[1].error

    def test_export_empty_source_error(self, manager, python_session, output_dir):
        result = manager.export_files(
            python_session,
            [{"source": "", "destination": "x.txt"}],
            output_dir=str(output_dir),
        )

        assert result.success is False
        assert result.files[0].success is False
        assert "source is required" in result.files[0].error

    def test_export_directory(self, manager, python_session, output_dir):
        manager.execute_terminal(python_session, "mkdir -p /workspace/mydir")
        _create_file_in_sandbox(manager, python_session, "mydir/f1.txt", "file1")
        _create_file_in_sandbox(manager, python_session, "mydir/f2.txt", "file2")

        result = manager.export_files(
            python_session,
            [{"source": "mydir", "destination": "mydir"}],
            output_dir=str(output_dir),
        )

        assert result.success is True
        exported_dir = output_dir / python_session / "mydir"
        assert exported_dir.is_dir()
        child_names = {f.name for f in exported_dir.rglob("*") if f.is_file()}
        assert "f1.txt" in child_names
        assert "f2.txt" in child_names

    def test_export_creates_session_subdirectory(self, manager, python_session, output_dir):
        _create_file_in_sandbox(manager, python_session, "sid.txt", "session dir test")

        result = manager.export_files(
            python_session,
            [{"source": "sid.txt", "destination": "sid.txt"}],
            output_dir=str(output_dir),
        )

        assert result.success is True
        session_dir = output_dir / python_session
        assert session_dir.is_dir()
        assert (session_dir / "sid.txt").exists()
        assert "session dir test" in (session_dir / "sid.txt").read_text()

    def test_export_invalid_session(self, manager, output_dir):
        with pytest.raises(ValueError, match="not found"):
            manager.export_files(
                "nonexistent_id",
                [{"source": "x", "destination": "x"}],
                output_dir=str(output_dir),
            )


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
        out_dir = Path(tempfile.mkdtemp(prefix="sandbox_tool_export_"))
        try:
            create = tools["create_session"]
            execute_terminal = tools["execute_terminal"]
            export = tools["export_files"]
            stop = tools["stop_session"]

            r = json.loads(create.invoke({"language": "python"}))
            sid = r["session_id"]

            try:
                json.loads(execute_terminal.invoke({
                    "session_id": sid,
                    "command": f"echo 'tool_export_test' > /workspace/tool_file.txt",
                }))

                # Temporarily patch OUTPUT_DIR for this test
                from sandbox_agent.settings import get_settings
                settings = get_settings()
                original = settings.OUTPUT_DIR
                settings.OUTPUT_DIR = str(out_dir)

                try:
                    r = json.loads(export.invoke({
                        "session_id": sid,
                        "files": [{"source": "tool_file.txt", "destination": "tool_file.txt"}],
                    }))
                finally:
                    settings.OUTPUT_DIR = original

                assert r["success"] is True
                assert len(r["files"]) == 1
                assert r["files"][0]["success"] is True
                assert (out_dir / sid / "tool_file.txt").exists()

            finally:
                json.loads(stop.invoke({"session_id": sid}))

        finally:
            shutil.rmtree(out_dir, ignore_errors=True)


# ── MCP tool-level tests ──────────────────────────────────────


class TestExportFilesMCP:
    def test_mcp_export(self, manager, python_session, output_dir):
        """Test the MCP export_files function directly (not via MCP transport)."""
        _create_file_in_sandbox(manager, python_session, "mcp_test.txt", "mcp_data")

        from sandbox_agent import mcp_server

        original_get = mcp_server._get_manager
        mcp_server._get_manager = lambda: manager

        try:
            result = mcp_server.export_files(
                session_id=python_session,
                files=[{"source": "mcp_test.txt", "destination": "mcp_test.txt"}],
                output_dir=str(output_dir),
            )
        finally:
            mcp_server._get_manager = original_get

        assert result["success"] is True
        assert len(result["files"]) == 1
        assert result["files"][0]["success"] is True
        assert (output_dir / python_session / "mcp_test.txt").exists()
        assert "mcp_data" in (output_dir / python_session / "mcp_test.txt").read_text()

    def test_mcp_export_returns_mappings(self, manager, python_session, output_dir):
        """Verify the output includes source/destination mappings usable for cross-session transfer."""
        _create_file_in_sandbox(manager, python_session, "transfer.csv", "1,2,3")

        from sandbox_agent import mcp_server

        original_get = mcp_server._get_manager
        mcp_server._get_manager = lambda: manager

        try:
            result = mcp_server.export_files(
                session_id=python_session,
                files=[{"source": "transfer.csv", "destination": "transfer.csv"}],
                output_dir=str(output_dir),
            )
        finally:
            mcp_server._get_manager = original_get

        file_entry = result["files"][0]
        assert "source" in file_entry
        assert "destination" in file_entry
        assert Path(file_entry["destination"]).is_absolute()
