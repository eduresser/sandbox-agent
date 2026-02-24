"""Tool: execute_code — executes code in a sandbox with persistent state."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._core import execute_code as core_execute_code


def create_execute_code_tool(manager: SandboxManager) -> BaseTool:
    @tool
    def execute_code(session_id: str, code: str, timeout: int | None = None) -> str:
        """Executes code in the sandbox. State persists across calls
        (like Jupyter Notebook cells).

        Args:
            session_id: ID returned by create_session.
            code: Code to execute (Python, Node.js, R, or Julia, depending on the session runtime).
            timeout: Max execution time in seconds (max 300). Defaults to 30.

        Returns:
            JSON with success, stdout, stderr, result, error, and figures.
        """
        return json.dumps(
            core_execute_code(manager, session_id, code, timeout),
            ensure_ascii=False,
        )

    return execute_code
