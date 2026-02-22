"""Tests for LangChain tool factories.

Requires Docker to be running on the host.
Run with: pytest tests/test_tools.py -v
"""

from __future__ import annotations

import json

import pytest

from sandbox_agent.sandbox.manager import SandboxManager
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


@pytest.fixture(scope="module")
def tools(manager: SandboxManager):
    return {t.name: t for t in create_tools(manager)}


class TestToolCreation:
    def test_all_tools_created(self, tools):
        expected = {
            "create_session",
            "execute_code",
            "execute_terminal",
            "import_files",
            "export_files",
            "stop_session",
        }
        assert set(tools.keys()) == expected

    def test_tools_have_descriptions(self, tools):
        for name, tool in tools.items():
            assert tool.description, f"Tool {name} has no description"


class TestToolIntegration:
    def test_full_workflow(self, tools):
        create = tools["create_session"]
        execute = tools["execute_code"]
        terminal = tools["execute_terminal"]
        stop = tools["stop_session"]

        result = json.loads(create.invoke({"language": "python", "dependencies": {}}))
        sid = result["session_id"]
        assert sid

        try:
            r = json.loads(execute.invoke({"session_id": sid, "code": "x = 42\nprint(x)"}))
            assert r["success"] is True
            assert "42" in r["stdout"]

            r = json.loads(execute.invoke({"session_id": sid, "code": "x * 2"}))
            assert r["success"] is True
            assert "84" in r["result"]["text/plain"]

            r = json.loads(terminal.invoke({"session_id": sid, "command": "echo hello"}))
            assert r["exit_code"] == 0
            assert "hello" in r["stdout"]

        finally:
            r = json.loads(stop.invoke({"session_id": sid}))
            assert r["success"] is True
