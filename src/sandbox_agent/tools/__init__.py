"""LangChain tools for the Sandbox Agent.

Tools are created via factory functions that bind to a shared
:class:`~sandbox_agent.sandbox.SandboxManager` instance.
"""

from __future__ import annotations

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools.create_session import create_create_session_tool
from sandbox_agent.tools.execute_code import create_execute_code_tool
from sandbox_agent.tools.execute_terminal import create_execute_terminal_tool
from sandbox_agent.tools.export_files import create_export_files_tool
from sandbox_agent.tools.import_files import create_import_files_tool
from sandbox_agent.tools.stop_session import create_stop_session_tool


def create_tools(manager: SandboxManager) -> list:
    """Create all agent tools bound to the given SandboxManager.

    Args:
        manager: The shared SandboxManager instance.

    Returns:
        List of LangChain tool callables.
    """
    return [
        create_create_session_tool(manager),
        create_execute_code_tool(manager),
        create_execute_terminal_tool(manager),
        create_import_files_tool(manager),
        create_export_files_tool(manager),
        create_stop_session_tool(manager),
    ]


__all__ = ["create_tools"]
