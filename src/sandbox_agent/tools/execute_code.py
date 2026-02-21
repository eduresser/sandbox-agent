"""Tool: execute_code — executes code in a sandbox with persistent state."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._helpers import error_response


def create_execute_code_tool(manager: SandboxManager):
    @tool
    def execute_code(session_id: str, code: str, timeout: int | None = None) -> str:
        """Executes code in the sandbox. State persists across calls
        (like Jupyter Notebook cells).

        Args:
            session_id: ID returned by create_session.
            code: Code to execute (Python, Node.js, or R, depending on the session runtime).
            timeout: Max execution time in seconds (max 300). Defaults to 30.

        Returns:
            JSON with success, stdout, stderr, result, error, and figures.
        """
        try:
            result = manager.execute_code(session_id, code, timeout=timeout or 30)
        except Exception as exc:
            return error_response(manager, exc)

        return json.dumps(
            {
                "success": result.success,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "result": result.result,
                "error": result.error,
                "figures": result.figures,
            },
            ensure_ascii=False,
        )

    return execute_code
