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


class TestNodeRuntime:
    """Tests for the Node.js runtime — exercises kernel_node.js specifics."""

    def test_create_node_session(self, manager: SandboxManager):
        info = manager.create_session(runtime="node")
        try:
            assert info.session_id in manager.sessions
            assert info.runtime == "node"
            assert info.status == "running"
        finally:
            manager.stop_session(info.session_id)

    def test_simple_expression(self, manager: SandboxManager, node_session: str):
        result = manager.execute_code(node_session, "1 + 1")
        assert isinstance(result, ExecutionResult)
        assert result.success is True
        assert result.result is not None
        assert "2" in result.result["text/plain"]

    def test_stdout_capture(self, manager: SandboxManager, node_session: str):
        result = manager.execute_code(node_session, 'console.log("hello world")')
        assert result.success is True
        assert "hello world" in result.stdout

    def test_stderr_capture(self, manager: SandboxManager, node_session: str):
        result = manager.execute_code(node_session, 'console.error("oops")')
        assert result.success is True
        assert "oops" in result.stderr

    def test_persistent_state(self, manager: SandboxManager, node_session: str):
        manager.execute_code(node_session, "var x = 42")
        result = manager.execute_code(node_session, "x * 2")
        assert result.success is True
        assert "84" in result.result["text/plain"]

    def test_const_let_redeclaration(self, manager: SandboxManager, node_session: str):
        """const/let are rewritten to var so re-declaration across cells works."""
        r1 = manager.execute_code(node_session, "const greeting = 'hello'")
        assert r1.success is True
        r2 = manager.execute_code(node_session, "const greeting = 'world'")
        assert r2.success is True
        r3 = manager.execute_code(node_session, "greeting")
        assert r3.success is True
        assert "world" in r3.result["text/plain"]

    def test_error_handling(self, manager: SandboxManager, node_session: str):
        result = manager.execute_code(node_session, 'throw new Error("boom")')
        assert result.success is False
        assert result.error is not None
        assert "Error" in result.error["type"]
        assert "boom" in result.error["message"]

    def test_reference_error(self, manager: SandboxManager, node_session: str):
        result = manager.execute_code(node_session, "undeclaredVariable")
        assert result.success is False
        assert result.error is not None
        assert "ReferenceError" in result.error["type"]

    def test_type_error(self, manager: SandboxManager, node_session: str):
        result = manager.execute_code(node_session, "null.foo")
        assert result.success is False
        assert "TypeError" in result.error["type"]

    def test_timeout(self, manager: SandboxManager, node_session: str):
        result = manager.execute_code(node_session, "while(true) {}", timeout=2)
        assert result.success is False

    def test_async_await(self, manager: SandboxManager, node_session: str):
        code = "await new Promise(resolve => setTimeout(() => resolve(99), 100))"
        result = manager.execute_code(node_session, code)
        assert result.success is True
        assert result.result is not None
        assert "99" in result.result["text/plain"]

    def test_promise_result(self, manager: SandboxManager, node_session: str):
        code = "Promise.resolve(42)"
        result = manager.execute_code(node_session, code)
        assert result.success is True
        assert result.result is not None
        assert "42" in result.result["text/plain"]

    def test_object_result(self, manager: SandboxManager, node_session: str):
        result = manager.execute_code(node_session, '({name: "test", value: 123})')
        assert result.success is True
        assert result.result is not None
        assert "test" in result.result["text/plain"]
        assert "123" in result.result["text/plain"]

    def test_array_result(self, manager: SandboxManager, node_session: str):
        result = manager.execute_code(node_session, "[1, 2, 3]")
        assert result.success is True
        assert result.result is not None
        plain = result.result["text/plain"]
        assert "1" in plain and "2" in plain and "3" in plain

    def test_require_builtin(self, manager: SandboxManager, node_session: str):
        code = 'const path = require("path"); path.join("/workspace", "test.txt")'
        result = manager.execute_code(node_session, code)
        assert result.success is True
        assert result.result is not None
        assert "test.txt" in result.result["text/plain"]

    def test_multiline_function(self, manager: SandboxManager, node_session: str):
        code = "function add(a, b) { return a + b; }\nadd(3, 4)"
        result = manager.execute_code(node_session, code)
        assert result.success is True
        assert "7" in result.result["text/plain"]

    def test_terminal_ls(self, manager: SandboxManager, node_session: str):
        result = manager.execute_terminal(node_session, "ls /workspace")
        assert isinstance(result, TerminalResult)
        assert result.exit_code == 0

    def test_install_packages(self, manager: SandboxManager):
        info = manager.create_session(runtime="node", dependencies={"lodash": ""})
        try:
            result = manager.execute_code(
                info.session_id,
                'const _ = require("lodash"); _.sum([1, 2, 3])',
            )
            assert result.success is True
            assert result.result is not None
            assert "6" in result.result["text/plain"]
        finally:
            manager.stop_session(info.session_id)

    def test_upload_and_read_file(self, manager: SandboxManager, node_session: str):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"key": "value"}')
            f.flush()
            local_path = f.name

        result = manager.upload_file(node_session, local_path)
        assert result["success"] is True

        filename = Path(local_path).name
        code = f'JSON.parse(require("fs").readFileSync("/workspace/{filename}", "utf8"))'
        exec_result = manager.execute_code(node_session, code)
        assert exec_result.success is True
        assert "value" in exec_result.result["text/plain"]


class TestJuliaRuntime:
    """Tests for the Julia runtime — exercises kernel_julia.jl specifics."""

    def test_create_julia_session(self, manager: SandboxManager):
        info = manager.create_session(runtime="julia")
        try:
            assert info.session_id in manager.sessions
            assert info.runtime == "julia"
            assert info.status == "running"
        finally:
            manager.stop_session(info.session_id)

    def test_simple_expression(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_code(julia_session, "1 + 1")
        assert isinstance(result, ExecutionResult)
        assert result.success is True
        assert result.result is not None
        assert "2" in result.result["text/plain"]

    def test_stdout_capture(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_code(julia_session, 'println("hello world")')
        assert result.success is True
        assert "hello world" in result.stdout

    def test_stderr_capture(self, manager: SandboxManager, julia_session: str):
        code = 'println(stderr, "warn message")'
        result = manager.execute_code(julia_session, code)
        assert result.success is True
        assert "warn message" in result.stderr

    def test_persistent_state(self, manager: SandboxManager, julia_session: str):
        manager.execute_code(julia_session, "x = 42")
        result = manager.execute_code(julia_session, "x * 2")
        assert result.success is True
        assert "84" in result.result["text/plain"]

    def test_error_handling(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_code(julia_session, 'error("test error")')
        assert result.success is False
        assert result.error is not None
        assert "ErrorException" in result.error["type"]
        assert "test error" in result.error["message"]

    def test_undefined_variable(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_code(julia_session, "nonexistent_var")
        assert result.success is False
        assert result.error is not None
        assert "UndefVarError" in result.error["type"]

    def test_method_error(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_code(julia_session, '"hello" + 1')
        assert result.success is False
        assert result.error is not None
        assert "MethodError" in result.error["type"]

    def test_timeout(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_code(julia_session, "while true end", timeout=2)
        assert result.success is False

    def test_multiline_function(self, manager: SandboxManager, julia_session: str):
        code = """
function fib(n)
    n <= 1 && return n
    fib(n-1) + fib(n-2)
end
fib(10)
"""
        result = manager.execute_code(julia_session, code)
        assert result.success is True
        assert "55" in result.result["text/plain"]

    def test_array_operations(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_code(julia_session, "sum([1, 2, 3, 4, 5])")
        assert result.success is True
        assert "15" in result.result["text/plain"]

    def test_string_interpolation(self, manager: SandboxManager, julia_session: str):
        code = 'name = "Julia"; println("Hello, $name!")'
        result = manager.execute_code(julia_session, code)
        assert result.success is True
        assert "Hello, Julia!" in result.stdout

    def test_multiple_dispatch(self, manager: SandboxManager, julia_session: str):
        code = """
greet(x::Int) = "Got integer: $x"
greet(x::String) = "Got string: $x"
greet(42)
"""
        result = manager.execute_code(julia_session, code)
        assert result.success is True
        assert "Got integer: 42" in result.result["text/plain"]

    def test_comprehension(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_code(julia_session, "[x^2 for x in 1:5]")
        assert result.success is True
        plain = result.result["text/plain"]
        assert "1" in plain and "4" in plain and "9" in plain and "16" in plain and "25" in plain

    def test_tuple_result(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_code(julia_session, "(1, 2, 3)")
        assert result.success is True
        assert result.result is not None
        plain = result.result["text/plain"]
        assert "1" in plain and "2" in plain and "3" in plain

    def test_dict_operations(self, manager: SandboxManager, julia_session: str):
        code = 'd = Dict("a" => 1, "b" => 2); d["a"] + d["b"]'
        result = manager.execute_code(julia_session, code)
        assert result.success is True
        assert "3" in result.result["text/plain"]

    def test_terminal_ls(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_terminal(julia_session, "ls /workspace")
        assert isinstance(result, TerminalResult)
        assert result.exit_code == 0

    def test_terminal_julia_version(self, manager: SandboxManager, julia_session: str):
        result = manager.execute_terminal(julia_session, "julia --version")
        assert result.exit_code == 0
        assert "julia" in result.stdout.lower()

    def test_install_packages(self, manager: SandboxManager):
        info = manager.create_session(runtime="julia", dependencies={"Statistics": ""})
        try:
            result = manager.execute_code(
                info.session_id,
                "using Statistics; mean([1, 2, 3, 4, 5])",
            )
            assert result.success is True
            assert "3" in result.result["text/plain"]
        finally:
            manager.stop_session(info.session_id)

    def test_upload_and_read_file(self, manager: SandboxManager, julia_session: str):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello from julia test")
            f.flush()
            local_path = f.name

        result = manager.upload_file(julia_session, local_path)
        assert result["success"] is True

        filename = Path(local_path).name
        code = f'read("/workspace/{filename}", String)'
        exec_result = manager.execute_code(julia_session, code)
        assert exec_result.success is True
        assert "hello from julia test" in exec_result.result["text/plain"]


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
