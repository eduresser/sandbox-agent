"""Utility helpers for message parsing, file handling, and session tracking."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UPLOAD_DIR = Path("./uploads")
OUTPUT_DIR = Path("./outputs")

FILE_ICONS: dict[str, str] = {
    ".py": "\U0001f40d",
    ".js": "\U0001f7e8",
    ".ts": "\U0001f535",
    ".r": "\U0001f4ca",
    ".jl": "\U0001f7e3",
    ".csv": "\U0001f4c4",
    ".tsv": "\U0001f4c4",
    ".json": "\U0001f4cb",
    ".xml": "\U0001f4cb",
    ".yaml": "\u2699\ufe0f",
    ".yml": "\u2699\ufe0f",
    ".txt": "\U0001f4dd",
    ".md": "\U0001f4dd",
    ".html": "\U0001f310",
    ".css": "\U0001f3a8",
    ".sql": "\U0001f5c3\ufe0f",
    ".png": "\U0001f5bc\ufe0f",
    ".jpg": "\U0001f5bc\ufe0f",
    ".jpeg": "\U0001f5bc\ufe0f",
    ".gif": "\U0001f5bc\ufe0f",
    ".svg": "\U0001f5bc\ufe0f",
    ".pdf": "\U0001f4d5",
    ".xlsx": "\U0001f4ca",
    ".xls": "\U0001f4ca",
    ".parquet": "\U0001f4e6",
    ".zip": "\U0001f4e6",
    ".tar": "\U0001f4e6",
    ".gz": "\U0001f4e6",
}


def get_file_icon(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return FILE_ICONS.get(ext, "\U0001f4ce")


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


# ── Time Formatting ─────────────────────────────────────


def time_ago(iso_str: str) -> str:
    """Convert an ISO 8601 timestamp to a human-readable relative time."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        seconds = int(delta.total_seconds())
    except Exception:
        return ""

    if seconds < 60:
        return "agora"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days < 30:
        return f"{days}d"
    months = days // 30
    return f"{months} mes{'es' if months > 1 else ''}"


# ── File Upload ─────────────────────────────────────────


def save_uploaded_files(
    thread_id: str,
    files: list,
) -> list[dict[str, Any]]:
    """Save Streamlit UploadedFile objects to disk.

    Returns a list of dicts with keys: name, path, size.
    """
    dest_dir = UPLOAD_DIR / thread_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    saved: list[dict[str, Any]] = []
    for f in files:
        file_path = dest_dir / f.name
        file_path.write_bytes(f.getvalue())
        saved.append({
            "name": f.name,
            "path": str(file_path.resolve()),
            "size": f.size,
        })
    return saved


def build_user_content(text: str, file_metas: list[dict[str, Any]]) -> str:
    """Build the message content combining user text and file references."""
    if not file_metas:
        return text

    file_block_parts = []
    for fm in file_metas:
        file_block_parts.append(
            f"- `{fm['name']}` ({format_file_size(fm['size'])}) "
            f"saved at `{fm['path']}`"
        )
    file_block = "\n".join(file_block_parts)

    parts = []
    if text:
        parts.append(text)
    parts.append(f"\n\n**Uploaded files:**\n{file_block}")
    return "\n".join(parts)


# ── Message Parsing ─────────────────────────────────────


@dataclass
class ParsedToolResult:
    """Structured representation of a parsed ToolMessage."""

    tool_name: str = ""
    raw_data: Any = None
    figures_b64: list[str] = field(default_factory=list)
    file_results: list[dict[str, Any]] = field(default_factory=list)
    session_info: dict[str, Any] | None = None
    text_summary: str = ""


def parse_tool_message(message: dict) -> ParsedToolResult:
    """Extract structured data from a serialized ToolMessage."""
    result = ParsedToolResult(tool_name=message.get("name", ""))
    content = message.get("content", "")

    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    if url.startswith("data:image"):
                        b64 = url.split(",", 1)[-1] if "," in url else url
                        result.figures_b64.append(b64)
                elif block.get("type") == "text":
                    _parse_tool_json(block.get("text", ""), result)
        return result

    if isinstance(content, str):
        _parse_tool_json(content, result)

    return result


def _parse_tool_json(raw: str, result: ParsedToolResult) -> None:
    """Try to parse a JSON string from tool output and populate result."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        result.text_summary = raw
        return

    result.raw_data = data
    if not isinstance(data, dict):
        result.text_summary = raw
        return

    for fig in data.get("figures", []) or []:
        if isinstance(fig, str) and fig:
            result.figures_b64.append(fig)

    if "files" in data and isinstance(data["files"], list):
        result.file_results = data["files"]

    if result.tool_name == "create_session":
        result.session_info = {
            "session_id": data.get("session_id", ""),
            "runtime": data.get("runtime", ""),
            "status": data.get("status", ""),
        }
    elif result.tool_name == "stop_session":
        result.session_info = {
            "session_id": data.get("session_id", ""),
            "status": "stopped",
        }

    parts = []
    if data.get("success") is not None:
        parts.append(f"success={data['success']}")
    if data.get("stdout"):
        stdout = data["stdout"]
        if len(stdout) > 500:
            stdout = stdout[:500] + "..."
        parts.append(f"stdout:\n```\n{stdout}\n```")
    if data.get("stderr"):
        stderr = data["stderr"]
        if len(stderr) > 300:
            stderr = stderr[:300] + "..."
        parts.append(f"stderr:\n```\n{stderr}\n```")
    if data.get("error"):
        err = data["error"]
        if isinstance(err, dict):
            parts.append(f"error: {err.get('type', '')}: {err.get('message', '')}")
        else:
            parts.append(f"error: {err}")
    if data.get("result"):
        parts.append(f"result: `{json.dumps(data['result'], ensure_ascii=False)[:200]}`")

    result.text_summary = "\n".join(parts)


# ── Session Tracking ────────────────────────────────────


@dataclass
class SessionStatus:
    session_id: str
    runtime: str = ""
    status: str = "unknown"


def extract_sessions_from_messages(messages: list[dict]) -> dict[str, SessionStatus]:
    """Walk through messages and track session create/stop events."""
    sessions: dict[str, SessionStatus] = {}

    for msg in messages:
        if msg.get("type") not in ("tool", "ToolMessage"):
            continue
        parsed = parse_tool_message(msg)
        if parsed.session_info:
            sid = parsed.session_info.get("session_id", "")
            if not sid:
                continue
            if sid in sessions:
                sessions[sid].status = parsed.session_info.get("status", sessions[sid].status)
            else:
                sessions[sid] = SessionStatus(
                    session_id=sid,
                    runtime=parsed.session_info.get("runtime", ""),
                    status=parsed.session_info.get("status", "unknown"),
                )

    return sessions


# ── Exported File Helpers ───────────────────────────────


def check_exported_file(path: str) -> bool:
    """Check whether an exported file exists on the host."""
    return os.path.isfile(path)


def read_exported_file(path: str) -> bytes | None:
    """Read an exported file's bytes, or None if inaccessible."""
    try:
        return Path(path).read_bytes()
    except Exception:
        return None


def decode_b64_image(b64_str: str) -> bytes:
    """Decode a base64-encoded image string to raw bytes."""
    return base64.b64decode(b64_str)
