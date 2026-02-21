"""Integration tests for SandboxManager.

Requires Docker to be running on the host.
Run with: pytest tests/test_manager.py -v
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.sandbox.models import ExecutionResult, TerminalResult


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


class TestCreateSession:
    def test_create_python_session(self, manager: SandboxManager):
        info = manager.create_session(runtime="python")
        try:
            assert info.session_id in manager.sessions
            assert info.runtime == "python"
            assert info.status == "running"
        finally:
            manager.stop_session(info.session_id)

    def test_create_node_session(self, manager: SandboxManager):
        info = manager.create_session(runtime="node")
        try:
            assert info.session_id in manager.sessions
            assert info.runtime == "node"
            assert info.status == "running"
        finally:
            manager.stop_session(info.session_id)

    def test_create_session_with_deps(self, manager: SandboxManager):
        info = manager.create_session(runtime="python", dependencies={"requests": ""})
        try:
            assert "requests" in info.dependencies
        finally:
            manager.stop_session(info.session_id)

    def test_invalid_runtime(self, manager: SandboxManager):
        with pytest.raises(ValueError, match="not supported"):
            manager.create_session(runtime="ruby")


class TestExecuteCode:
    def test_simple_expression(self, manager: SandboxManager, python_session: str):
        result = manager.execute_code(python_session, "1 + 1")
        assert isinstance(result, ExecutionResult)
        assert result.success is True
        assert result.result is not None
        assert "2" in result.result["text/plain"]

    def test_stdout_capture(self, manager: SandboxManager, python_session: str):
        result = manager.execute_code(python_session, 'print("hello world")')
        assert result.success is True
        assert "hello world" in result.stdout

    def test_persistent_state(self, manager: SandboxManager, python_session: str):
        manager.execute_code(python_session, "x = 42")
        result = manager.execute_code(python_session, "x * 2")
        assert result.success is True
        assert "84" in result.result["text/plain"]

    def test_error_handling(self, manager: SandboxManager, python_session: str):
        result = manager.execute_code(python_session, "1 / 0")
        assert result.success is False
        assert result.error is not None
        assert "ZeroDivisionError" in result.error["type"]

    def test_timeout(self, manager: SandboxManager, python_session: str):
        result = manager.execute_code(
            python_session, "import time; time.sleep(60)", timeout=2
        )
        assert result.success is False


class TestExecuteTerminal:
    def test_ls(self, manager: SandboxManager, python_session: str):
        result = manager.execute_terminal(python_session, "ls /workspace")
        assert isinstance(result, TerminalResult)
        assert result.exit_code == 0

    def test_echo(self, manager: SandboxManager, python_session: str):
        result = manager.execute_terminal(python_session, "echo 'test123'")
        assert result.exit_code == 0
        assert "test123" in result.stdout

    def test_failed_command(self, manager: SandboxManager, python_session: str):
        result = manager.execute_terminal(python_session, "false")
        assert result.exit_code != 0


class TestInstallPackages:
    def test_install_via_terminal(self, manager: SandboxManager, python_session: str):
        result = manager.execute_terminal(
            python_session, "pip install --no-cache-dir requests"
        )
        assert result.exit_code == 0
        assert "Successfully installed" in result.stdout or "already satisfied" in result.stdout

        code_result = manager.execute_code(
            python_session,
            "import requests; print(requests.__version__)",
        )
        assert code_result.success is True
        assert code_result.stdout.strip()

    def test_initial_deps_installed(self, manager: SandboxManager):
        info = manager.create_session(runtime="python", dependencies={"requests": ""})
        try:
            result = manager.execute_code(
                info.session_id,
                "import requests; print(requests.__version__)",
            )
            assert result.success is True
            assert result.stdout.strip()
        finally:
            manager.stop_session(info.session_id)


class TestUploadFiles:
    def test_upload_and_verify(self, manager: SandboxManager, python_session: str):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("a,b,c\n1,2,3\n")
            f.flush()
            local_path = f.name

        result = manager.upload_file(python_session, local_path)
        assert result["success"] is True

        ls_result = manager.execute_terminal(python_session, "ls /workspace/")
        assert Path(local_path).name in ls_result.stdout


class TestRRuntime:
    """Tests for the R runtime — mirrors the Python test suite."""

    def test_create_r_session(self, manager: SandboxManager):
        info = manager.create_session(runtime="r")
        try:
            assert info.session_id in manager.sessions
            assert info.runtime == "r"
            assert info.status == "running"
        finally:
            manager.stop_session(info.session_id)

    def test_simple_expression(self, manager: SandboxManager, r_session: str):
        result = manager.execute_code(r_session, "1 + 1")
        assert isinstance(result, ExecutionResult)
        assert result.success is True
        assert result.result is not None
        assert "2" in result.result["text/plain"]

    def test_stdout_capture(self, manager: SandboxManager, r_session: str):
        result = manager.execute_code(r_session, 'cat("hello world")')
        assert result.success is True
        assert "hello world" in result.stdout

    def test_persistent_state(self, manager: SandboxManager, r_session: str):
        manager.execute_code(r_session, "x <- 42")
        result = manager.execute_code(r_session, "x * 2")
        assert result.success is True
        assert "84" in result.result["text/plain"]

    def test_error_handling(self, manager: SandboxManager, r_session: str):
        result = manager.execute_code(r_session, 'stop("test error")')
        assert result.success is False
        assert result.error is not None
        assert "simpleError" in result.error["type"] or "error" in result.error["type"].lower()

    def test_timeout(self, manager: SandboxManager, r_session: str):
        result = manager.execute_code(r_session, "repeat { x <- 1 }", timeout=2)
        assert result.success is False

    def test_terminal_ls(self, manager: SandboxManager, r_session: str):
        result = manager.execute_terminal(r_session, "ls /workspace")
        assert isinstance(result, TerminalResult)
        assert result.exit_code == 0

    def test_install_packages(self, manager: SandboxManager):
        info = manager.create_session(runtime="r", dependencies={"crayon": ""})
        try:
            result = manager.execute_code(
                info.session_id,
                'library(crayon); cat(green("ok"))',
            )
            assert result.success is True
            assert "ok" in result.stdout
        finally:
            manager.stop_session(info.session_id)


class TestStopSession:
    def test_stop_removes_session(self, manager: SandboxManager):
        info = manager.create_session(runtime="python")
        sid = info.session_id
        assert sid in manager.sessions

        manager.stop_session(sid)
        assert sid not in manager.sessions

    def test_stop_nonexistent(self, manager: SandboxManager):
        assert manager.stop_session("nonexistent") is False


class TestCleanup:
    def test_cleanup_all(self):
        mgr = SandboxManager()
        mgr.create_session(runtime="python")
        mgr.create_session(runtime="python")

        assert len(mgr.sessions) == 2
        mgr.cleanup_all()
        assert len(mgr.sessions) == 0
