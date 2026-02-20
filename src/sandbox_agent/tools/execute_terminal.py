"""Tool: execute_terminal — runs shell commands inside a sandbox."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._helpers import error_response


def create_execute_terminal_tool(manager: SandboxManager):
    @tool
    def execute_terminal(session_id: str, command: str) -> str:
        """Runs a shell command inside the sandbox.
        Useful for listing files, installing Linux dependencies, etc.

        Args:
            session_id: ID returned by create_session.
            command: Shell command to execute (e.g. "ls -la", "apt-get install -y curl").

        Returns:
            JSON with stdout, stderr, and exit_code.
        """
        try:
            result = manager.execute_terminal(session_id, command)
        except Exception as exc:
            return error_response(manager, exc)

        return json.dumps(
            {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            ensure_ascii=False,
        )

    return execute_terminal
