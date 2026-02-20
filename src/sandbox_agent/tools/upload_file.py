"""Tool: upload_file — uploads a file from the host to the sandbox."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._helpers import error_response


def create_upload_file_tool(manager: SandboxManager):
    @tool
    def upload_file(
        session_id: str,
        local_path: str,
        remote_name: str | None = None,
    ) -> str:
        """Copies a file from the host machine into the sandbox (/workspace/).
        You have access to all host file paths through this tool.
        Requires an active session_id from create_session.

        Args:
            session_id: ID returned by create_session.
            local_path: Full path on the host (e.g. "/home/user/data.csv").
            remote_name: File name in the sandbox (optional, defaults to original name).

        Returns:
            JSON with remote_path and size.
        """
        try:
            result = manager.upload_file(session_id, local_path, remote_name)
        except Exception as exc:
            return error_response(manager, exc)

        return json.dumps(result, ensure_ascii=False)

    return upload_file
