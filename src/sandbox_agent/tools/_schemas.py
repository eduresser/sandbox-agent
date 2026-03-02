"""Pydantic input models for tool validation.

Each model validates the arguments of a core tool function before execution.
On validation failure the caller receives a structured error dict instead of
an unhandled exception.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class CreateSessionInput(BaseModel):
    language: Literal["python", "node", "r", "julia"] = "python"
    dependencies: dict[str, str] = Field(default_factory=dict)

    @field_validator("dependencies", mode="before")
    @classmethod
    def _coerce_dependency_values(cls, v: dict) -> dict[str, str]:
        if not isinstance(v, dict):
            return v
        return {k: "" if val is None else str(val) for k, val in v.items()}


class ExecuteCodeInput(BaseModel):
    session_id: Annotated[str, Field(min_length=1)]
    code: Annotated[str, Field(min_length=1)]
    timeout: Annotated[int | None, Field(ge=1, le=300)] = None


class ExecuteTerminalInput(BaseModel):
    session_id: Annotated[str, Field(min_length=1)]
    command: Annotated[str, Field(min_length=1)]


class ImportFileEntry(BaseModel):
    """A single entry in the ``files`` list for import_files."""

    source: str | None = None
    session_id: str | None = None
    path: str | None = None
    destination: str | None = None

    @field_validator("destination", mode="after")
    @classmethod
    def _check_mode(cls, v: str | None, info) -> str | None:  # noqa: ANN001
        data = info.data
        has_host = bool(data.get("source"))
        has_cross = bool(data.get("session_id")) and bool(data.get("path"))
        if not has_host and not has_cross:
            msg = (
                'Each file entry must provide either "source" (host mode) '
                'or both "session_id" and "path" (cross-session mode).'
            )
            raise ValueError(msg)
        return v


class ImportFilesInput(BaseModel):
    session_id: Annotated[str, Field(min_length=1)]
    files: Annotated[list[ImportFileEntry], Field(min_length=1)]


class ExportFileEntry(BaseModel):
    """A single entry in the ``files`` list for export_files."""

    source: Annotated[str, Field(min_length=1)]
    destination: str | None = None


class ExportFilesInput(BaseModel):
    session_id: Annotated[str, Field(min_length=1)]
    files: Annotated[list[ExportFileEntry], Field(min_length=1)]


class StopSessionInput(BaseModel):
    session_id: Annotated[str, Field(min_length=1)]
