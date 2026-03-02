"""Tool: execute_terminal — runs shell commands inside a sandbox."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._core import execute_terminal as core_execute_terminal


def create_execute_terminal_tool(manager: SandboxManager) -> BaseTool:
    @tool
    def execute_terminal(session_id: str = "", command: str = "") -> str:
        """Runs a shell command inside the sandbox.
        Useful for listing files, installing Linux dependencies, etc.

        Args:
            session_id: ID returned by create_session.
            command: Shell command to execute (e.g. "ls -la", "apt-get install -y curl").

        Returns:
            JSON with stdout, stderr, and exit_code.
        """
        return json.dumps(
            core_execute_terminal(manager, session_id, command),
            ensure_ascii=False,
        )

    return execute_terminal
