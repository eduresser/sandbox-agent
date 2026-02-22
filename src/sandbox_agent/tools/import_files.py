"""Tool: import_files — imports files and directories from the host into the sandbox."""

from __future__ import annotations

import json
from dataclasses import asdict

from langchain_core.tools import tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._helpers import error_response


def create_import_files_tool(manager: SandboxManager):
    @tool
    def import_files(
        session_id: str,
        files: list[dict[str, str]],
    ) -> str:
        """Copies one or more files or directories from the host machine into
        the sandbox (/workspace/).  You have access to all host file paths.
        Requires an active session_id from create_session.

        Args:
            session_id: ID returned by create_session.
            files: List of objects with "source" and "destination" keys.
                source: Full path on the host (e.g. "/home/user/data.csv" or
                    "/home/user/my_folder/").
                destination: Name or path inside the sandbox (relative to
                    /workspace/ or absolute). Defaults to the original name.
                Example: [{"source": "/home/user/data.csv", "destination": "data.csv"},
                          {"source": "/home/user/project/", "destination": "project/"}]

        Returns:
            JSON with per-file results (source, destination, success, size, error).
        """
        try:
            result = manager.import_files(session_id, list(files))
        except Exception as exc:
            return error_response(manager, exc)

        return json.dumps(asdict(result), ensure_ascii=False)

    return import_files
