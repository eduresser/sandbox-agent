"""Tool: stop_session — stops and removes a sandbox container."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._helpers import error_response


def create_stop_session_tool(manager: SandboxManager):
    @tool
    def stop_session(session_id: str) -> str:
        """Stops and removes the sandbox completely.
        Use when done with a session.

        Args:
            session_id: ID returned by create_session.

        Returns:
            JSON with success status.
        """
        try:
            success = manager.stop_session(session_id)
        except Exception as exc:
            return error_response(manager, exc)

        return json.dumps({"success": success}, ensure_ascii=False)

    return stop_session
