"""Data models for sandbox sessions and execution results."""

from __future__ import annotations

from dataclasses import dataclass, field

TRUNCATION_NOTICE = "\n\n... [truncated — {original} chars total, showing first {limit}]"


def truncate_field(value: str, max_chars: int) -> str:
    """Truncate *value* to *max_chars*, appending a notice if trimmed."""
    if not value or max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[:max_chars] + TRUNCATION_NOTICE.format(
        original=len(value), limit=max_chars,
    )


@dataclass
class SessionInfo:
    """Metadata for an active sandbox session (Docker container)."""

    session_id: str
    container_id: str
    container_name: str
    runtime: str
    status: str
    dependencies: dict[str, str] = field(default_factory=dict)
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
    """Result of exporting a single file from the sandbox to the host."""

    source: str
    destination: str
    success: bool
    size: int = 0
    error: str = ""


@dataclass
class ExportResult:
    """Aggregated result of an export_files operation."""

    success: bool
    files: list[ExportFileResult] = field(default_factory=list)
    error: str = ""
