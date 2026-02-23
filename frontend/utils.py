"""Utility helpers for message parsing, file handling, and session tracking."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandbox_agent.settings import get_settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_settings = get_settings()
STORAGE_DIR = (
    Path(_settings.STORAGE_DIR)
    if Path(_settings.STORAGE_DIR).is_absolute()
    else _PROJECT_ROOT / _settings.STORAGE_DIR
)

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

    Files are stored under STORAGE_DIR/<thread_id>/uploads/ (cleaned when thread is evicted).

    Returns a list of dicts with keys: name, path, size.
    """
    dest_dir = STORAGE_DIR / thread_id / "uploads"
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


# ── Tool Block Formatting (CLI-style) ─────────────────────

_MAX_TOOL_OUTPUT_LINES = 60

# Map runtime to st.code language (same as CLI _RUNTIME_LEXER)
_RUNTIME_LANGUAGE: dict[str, str] = {
    "python": "python",
    "node": "javascript",
    "r": "r",
    "julia": "julia",
}


def format_tool_input_display(
    tool_call: dict[str, Any],
    sessions: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Format tool input for display. Returns (text, language) for st.code.

    For execute_code: uses runtime from sessions (session_id -> runtime) when
    available; defaults to python. For execute_terminal: always bash.
    """
    name = tool_call.get("name", "?")
    args = tool_call.get("args", {})

    if name == "execute_code" and "code" in args:
        lang = "python"
        session_id = args.get("session_id", "")
        if sessions and session_id and session_id in sessions:
            lang = _RUNTIME_LANGUAGE.get(sessions[session_id], "python")
        return (args["code"], lang)
    if name == "execute_terminal" and "command" in args:
        return (args["command"], "bash")
    return (
        json.dumps(args, indent=2, ensure_ascii=False, default=str),
        "json",
    )


def _extract_tool_output_text(content: Any) -> str:
    """Extract displayable text from ToolMessage content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block["text"]
    return str(content)


def format_tool_output_display(content: Any, tool_name: str = "") -> tuple[str, bool]:
    """Format tool output for display. Returns (formatted_text, is_error)."""
    text_content = _extract_tool_output_text(content)

    try:
        parsed = json.loads(text_content) if isinstance(text_content, str) else text_content
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False, default=str)
    except (json.JSONDecodeError, TypeError):
        formatted = str(text_content)

    lines = formatted.splitlines()
    if len(lines) > _MAX_TOOL_OUTPUT_LINES:
        visible = lines[:_MAX_TOOL_OUTPUT_LINES]
        omitted = len(lines) - _MAX_TOOL_OUTPUT_LINES
        visible.append(f"\n... +{omitted} linhas omitidas ...")
        formatted = "\n".join(visible)

    is_error = False
    try:
        parsed_check = (
            json.loads(text_content) if isinstance(text_content, str) else text_content
        )
        if isinstance(parsed_check, dict) and parsed_check.get("success") is False:
            is_error = True
    except (json.JSONDecodeError, TypeError):
        pass

    if not is_error and isinstance(text_content, str) and "Error invoking tool" in text_content:
        is_error = True

    return (formatted, is_error)


def _extract_thought_from_content(content: Any) -> str:
    """Extract thinking/reasoning text from AIMessage content (list of blocks)."""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type", "")
        if t in ("thinking", "reasoning"):
            # Anthropic: thinking; OpenAI/LangChain: reasoning
            text = b.get("thinking") or b.get("reasoning") or b.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
            elif isinstance(text, list):
                # OpenAI reasoning summary can be a list of segments
                for seg in text:
                    if isinstance(seg, dict) and seg.get("type") == "text":
                        parts.append(seg.get("text", ""))
                    elif isinstance(seg, str):
                        parts.append(seg)
    return "\n\n".join(parts) if parts else ""


def collect_tool_blocks(messages: list[dict]) -> tuple[list[dict], str]:
    """Pair tool_calls with ToolMessages, extract thoughts, and final AI content.

    Returns (items, final_content) where items is a list of:
    - {"type": "thought", "text": "..."} — reasoning/thinking before tool calls
    - {"type": "block", "block": {...}} — tool input+output (output may be None)
    """
    items: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    final_content = ""

    for msg in messages:
        msg_type = msg.get("type", "")

        if msg_type in ("ai", "AIMessage", "AIMessageChunk"):
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])

            if tool_calls:
                thought = _extract_thought_from_content(content)
                if thought:
                    items.append({"type": "thought", "text": thought})
                for tc in tool_calls:
                    block = {
                        "name": tc.get("name", "?"),
                        "args": tc.get("args", {}),
                        "output": None,
                        "tool_call_id": tc.get("id", ""),
                    }
                    blocks.append(block)
                    items.append({"type": "block", "block": block})
            else:
                if isinstance(content, str) and content:
                    final_content = content
                elif isinstance(content, list):
                    text_parts = [
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    if text_parts:
                        final_content = "\n".join(text_parts)

        elif msg_type in ("tool", "ToolMessage"):
            tid = msg.get("tool_call_id", "")
            output_content = msg.get("content", "")
            for b in blocks:
                if b.get("tool_call_id") == tid:
                    b["output"] = output_content
                    if msg.get("name"):
                        b["name"] = msg["name"]
                    break

    return (items, final_content)


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
