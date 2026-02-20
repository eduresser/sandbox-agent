"""Data models for sandbox sessions and execution results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionInfo:
    """Metadata for an active sandbox session (Docker container)."""

    session_id: str
    container_id: str
    container_name: str
    runtime: str
    status: str
    dependencies: dict[str, str] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""


@dataclass
class ExecutionResult:
    """Result of a code execution inside a sandbox."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    result: dict | None = None
    error: dict | None = None
    figures: list[str] = field(default_factory=list)


@dataclass
class TerminalResult:
    """Result of a terminal command execution inside a sandbox."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
