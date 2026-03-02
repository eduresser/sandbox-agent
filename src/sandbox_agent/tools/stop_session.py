"""Tool: stop_session — stops and removes a sandbox container."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._core import stop_session as core_stop_session


def create_stop_session_tool(manager: SandboxManager) -> BaseTool:
    @tool
    def stop_session(session_id: str = "") -> str:
        """Stops and removes the sandbox completely.
        Use when done with a session.

        Args:
            session_id: ID returned by create_session.

        Returns:
            JSON with success status.
        """
        return json.dumps(
            core_stop_session(manager, session_id),
            ensure_ascii=False,
        )

    return stop_session
