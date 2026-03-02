"""Tool: import_files — imports files and directories from the host into the sandbox."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._core import import_files as core_import_files


def create_import_files_tool(manager: SandboxManager) -> BaseTool:
    @tool
    def import_files(
        session_id: str = "",
        files: list[dict[str, str]] = [],
    ) -> str:
        """Copies files into the sandbox from the host or from another session.

        Two modes:
        - Host: {"source": "<host path>", "destination": "..."}
        - Cross-session: {"session_id": "<src_session>", "path": "<container path>",
          "destination": "..."} — use files returned by export_files from another
          session in the same conversation.

        Args:
            session_id: ID returned by create_session (destination).
            files: List of file entries. For host: source + destination.
                For cross-session: session_id + path + destination.
                Example: [{"source": "/home/user/data.csv", "destination": "data.csv"},
                          {"session_id": "abc123", "path": "/workspace/out.csv",
                           "destination": "out.csv"}]

        Returns:
            JSON with per-file results (source, destination, success, size, error).
        """
        return json.dumps(
            core_import_files(manager, session_id, files),
            ensure_ascii=False,
        )

    return import_files
