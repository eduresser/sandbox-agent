"""Tool: create_session — creates an isolated sandbox container."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools._core import create_session as core_create_session


def create_create_session_tool(manager: SandboxManager) -> BaseTool:
    @tool
    def create_session(
        language: str = "python",
        dependencies: dict[str, str] = {},
    ) -> str:
        """Creates an isolated sandbox environment (Docker container).
        The sandbox starts empty — specify ALL needed packages in dependencies.
        For data analysis, always include pandas and numpy.

        Args:
            language: Sandbox language/runtime. Use "python", "node", "r", or "julia".
            dependencies: Packages to pre-install. Keys are package names,
                values are version strings (use "" for latest). Always use strings,
                never numbers — e.g. "2.2" not 2.2. Null/None is treated as "".
                Example: {"pandas": "", "numpy": "1.26", "matplotlib": "3.9.0"}
                (R example: {"ggplot2": "", "dplyr": ""}).

        Returns:
            JSON with session_id and session info.
        """
        return json.dumps(
            core_create_session(manager, language, dependencies),
            ensure_ascii=False,
        )

    return create_session
