"""Aegra API client with SSE streaming support.

Uses httpx for synchronous HTTP + streaming, communicating with the
LangGraph Platform compatible API served by Aegra.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Generator

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_ASSISTANT_ID = "sandbox-agent"
STREAM_TIMEOUT = httpx.Timeout(connect=5, read=300, write=10, pool=10)


@dataclass
class SSEEvent:
    """A single Server-Sent Event."""

    event: str
    data: Any


class AegraClient:
    """Synchronous client for the Aegra (LangGraph Platform) REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        assistant_id: str = DEFAULT_ASSISTANT_ID,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.assistant_id = assistant_id
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=STREAM_TIMEOUT,
        )

    def close(self) -> None:
        self._client.close()

    # ── Health ──────────────────────────────────────────

    def get_health(self) -> dict:
        r = self._client.get("/health")
        r.raise_for_status()
        return r.json()

    def is_healthy(self) -> bool:
        try:
            self.get_health()
            return True
        except Exception:
            return False

    # ── Threads ─────────────────────────────────────────

    def create_thread(self, metadata: dict | None = None) -> dict:
        r = self._client.post("/threads", json={"metadata": metadata or {}})
        r.raise_for_status()
        return r.json()

    def get_thread(self, thread_id: str) -> dict:
        r = self._client.get(f"/threads/{thread_id}")
        r.raise_for_status()
        return r.json()

    def list_threads(self, limit: int = 20) -> list[dict]:
        r = self._client.post("/threads/search", json={"limit": limit})
        r.raise_for_status()
        return r.json()

    def delete_thread(self, thread_id: str) -> None:
        r = self._client.delete(f"/threads/{thread_id}")
        r.raise_for_status()

    def get_thread_state(self, thread_id: str) -> dict:
        r = self._client.get(f"/threads/{thread_id}/state")
        r.raise_for_status()
        return r.json()

    def get_download_url(
        self,
        thread_id: str,
        session_id: str,
        path: str,
    ) -> str:
        """Return the URL to download an exported file (for use as link)."""
        from urllib.parse import quote

        encoded_path = quote(path, safe="")
        return f"{self.base_url}/threads/{thread_id}/files/download?session_id={session_id}&path={encoded_path}"

    def download_exported_file(
        self,
        thread_id: str,
        session_id: str,
        path: str,
    ) -> bytes:
        """Download a file exported from a sandbox session."""
        r = self._client.get(
            f"/threads/{thread_id}/files/download",
            params={"session_id": session_id, "path": path},
        )
        r.raise_for_status()
        return r.content

    # ── Runs (streaming) ────────────────────────────────

    def stream_run(
        self,
        thread_id: str,
        input_messages: list[dict],
        configurable: dict[str, str] | None = None,
    ) -> Generator[SSEEvent, None, None]:
        """Stream a run via SSE, yielding parsed events.

        Args:
            thread_id: The thread to run against.
            input_messages: List of message dicts, e.g.
                ``[{"role": "human", "content": "Hello"}]``.
            configurable: Optional dict passed as
                ``config.configurable`` (model overrides, etc.).

        Yields:
            SSEEvent with .event (str) and .data (parsed JSON or str).
        """
        payload: dict[str, Any] = {
            "assistant_id": self.assistant_id,
            "input": {"messages": input_messages},
            "stream_mode": ["values"],
        }
        if configurable:
            payload["config"] = {"configurable": configurable}

        with self._client.stream(
            "POST",
            f"/threads/{thread_id}/runs/stream",
            json=payload,
        ) as response:
            response.raise_for_status()
            yield from _parse_sse_stream(response)

    def run_and_wait(
        self,
        thread_id: str,
        input_messages: list[dict],
        configurable: dict[str, str] | None = None,
    ) -> dict:
        """Run synchronously (non-streaming) and return the final output."""
        payload: dict[str, Any] = {
            "assistant_id": self.assistant_id,
            "input": {"messages": input_messages},
        }
        if configurable:
            payload["config"] = {"configurable": configurable}

        r = self._client.post(
            f"/threads/{thread_id}/runs/wait",
            json=payload,
        )
        r.raise_for_status()
        return r.json()


def _parse_sse_stream(
    response: httpx.Response,
) -> Generator[SSEEvent, None, None]:
    """Parse an SSE byte stream into structured events."""
    event_type = ""
    data_lines: list[str] = []

    for line in response.iter_lines():
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line == "":
            if data_lines:
                raw = "\n".join(data_lines)
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    parsed = raw
                yield SSEEvent(event=event_type, data=parsed)
            event_type = ""
            data_lines = []

    if data_lines:
        raw = "\n".join(data_lines)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            parsed = raw
        yield SSEEvent(event=event_type, data=parsed)
