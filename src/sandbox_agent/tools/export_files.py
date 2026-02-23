"""Tool: export_files — exports files from the sandbox to the host."""

from __future__ import annotations

import json
from dataclasses import asdict

from langchain_core.tools import tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._helpers import error_response


def create_export_files_tool(manager: SandboxManager):
    @tool
    def export_files(
        session_id: str,
        files: list[dict[str, str]],
    ) -> str:
        """Registers files for download and cross-session import (no host copy).

        Files become available via the API download endpoint and for import_files
        in other sessions of the same conversation. The user can download via
        GET /threads/{thread_id}/files/download?session_id=...&path=...

        Args:
            session_id: ID returned by create_session.
            files: List of objects with "source" (and optional "destination").
                source: Path inside the container (relative to /workspace/ or absolute).
                Example: [{"source": "report.pdf"}, {"source": "/workspace/data.csv"}]

        Returns:
            JSON with per-file results (session_id, path, success, size, error).
            path is always absolute inside the container (e.g. /workspace/report.pdf).
        """
        try:
            result = manager.export_files(session_id, list(files))
        except Exception as exc:
            return error_response(manager, exc)

        return json.dumps(asdict(result), ensure_ascii=False)

    return export_files
