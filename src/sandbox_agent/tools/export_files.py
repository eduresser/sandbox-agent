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
        """Exports one or more files or directories from the sandbox to the host machine.

        The base output directory is configured via the OUTPUT_DIR setting
        (defaults to "./outputs"). Relative destination paths are resolved
        against that directory.

        Args:
            session_id: ID returned by create_session.
            files: List of objects with "source" and "destination" keys.
                source: Path inside the container (relative to /workspace/ or absolute).
                destination: Path on the host (relative to OUTPUT_DIR or absolute).
                    If omitted, the file keeps its original name inside OUTPUT_DIR.
                Example: [{"source": "report.pdf", "destination": "client/report.pdf"},
                          {"source": "/workspace/data.csv", "destination": "data.csv"}]

        Returns:
            JSON with per-file results (source, destination, success, size, error).
        """
        try:
            result = manager.export_files(session_id, list(files))
        except Exception as exc:
            return error_response(manager, exc)

        return json.dumps(asdict(result), ensure_ascii=False)

    return export_files
