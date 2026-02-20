"""Tool: create_session — creates an isolated sandbox container."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._helpers import error_response


def create_create_session_tool(manager: SandboxManager):
    @tool
    def create_session(
        language: str = "python",
        dependencies: dict[str, str] | None = None,
    ) -> str:
        """Creates an isolated sandbox environment (Docker container).
        The sandbox starts empty — specify ALL needed packages in dependencies.
        For data analysis, always include pandas and numpy.

        Args:
            language: Sandbox language/runtime. Use "python" or "node".
            dependencies: Packages to pre-install. Keys are package names,
                values are versions (use "" for latest).
                Example: {"pandas": "", "numpy": "", "matplotlib": "3.9.0"}.

        Returns:
            JSON with session_id and session info.
        """
        deps = dict(dependencies) if dependencies else {}

        try:
            info = manager.create_session(runtime=language or "python", dependencies=deps)
        except Exception as exc:
            return error_response(manager, exc)

        return json.dumps(
            {
                "success": True,
                "session_id": info.session_id,
                "runtime": info.runtime,
                "status": info.status,
                "dependencies": info.dependencies,
            },
            ensure_ascii=False,
        )

    return create_session
