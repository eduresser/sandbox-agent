"""Data models for sandbox sessions and execution results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

TRUNCATION_NOTICE = "\n\n... [truncated — {original} chars total, showing first {limit}]"

RUNTIME_LANGUAGE: dict[str, str] = {
    "python": "python",
    "node": "javascript",
    "r": "r",
    "julia": "julia",
}

MAX_TOOL_OUTPUT_LINES = 60


def truncate_field(value: str, max_chars: int) -> str:
    """Truncate *value* to *max_chars*, appending a notice if trimmed."""
    if not value or max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[:max_chars] + TRUNCATION_NOTICE.format(
        original=len(value), limit=max_chars,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SessionInfo:
    """Metadata for an active sandbox session (Docker container)."""

    session_id: str
    container_id: str
    container_name: str
    runtime: str
    status: str
    thread_id: str | None = None
    dependencies: dict[str, str] = field(default_factory=dict)
    stderr: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    last_activity: datetime = field(default_factory=_utcnow)


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


@dataclass
class ImportFileResult:
    """Result of importing a single file/directory from the host into the sandbox."""

    source: str
    destination: str
    success: bool
    size: int = 0
    error: str = ""


@dataclass
class ImportResult:
    """Aggregated result of an import_files operation."""

    success: bool
    files: list[ImportFileResult] = field(default_factory=list)
    error: str = ""


@dataclass
class ExportFileResult:
    """Result of exporting a single file from the sandbox (on-demand, no host copy).

    Files are registered as "released" for download via HTTP or cross-session import.
    path is always absolute inside the container (e.g. /workspace/report.pdf).
    download_url is set by the tool layer when the API is available.
    """

    session_id: str
    path: str  # absolute path inside container (e.g. /workspace/report.pdf)
    success: bool
    size: int = 0
    error: str = ""
    download_url: str | None = None


@dataclass
class ExportResult:
    """Aggregated result of an export_files operation."""

    success: bool
    files: list[ExportFileResult] = field(default_factory=list)
    error: str = ""
