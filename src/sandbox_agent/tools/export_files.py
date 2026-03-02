"""Tool: export_files — exports files from the sandbox to the host."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._core import export_files as core_export_files


def create_export_files_tool(manager: SandboxManager) -> BaseTool:
    @tool
    def export_files(
        session_id: str = "",
        files: list[dict[str, str]] = [],
    ) -> str:
        """Registers files for download and cross-session import (no host copy).

        Files become available via the API download endpoint and for import_files
        in other sessions of the same conversation. The user can download via
        GET /threads/{thread_id}/files/download?session_id=...&path=...
        (API must be running). Result includes download_url for each file.

        Args:
            session_id: ID returned by create_session.
            files: List of objects with "source" (and optional "destination").
                source: Path inside the container (relative to /workspace/ or absolute).
                Example: [{"source": "report.pdf"}, {"source": "/workspace/data.csv"}]

        Returns:
            JSON with per-file results (session_id, path, success, size, error,
            download_url). path is always absolute (e.g. /workspace/report.pdf).
        """
        return json.dumps(
            core_export_files(manager, session_id, files),
            ensure_ascii=False,
        )

    return export_files
