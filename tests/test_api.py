"""Integration tests for the Aegra REST API (LangGraph Platform).

Tests the full API lifecycle: threads, assistants, runs, and thread isolation.
Requires the Aegra dev server running on localhost:8000.

Run with: pytest tests/test_api.py -v -s
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

BASE_URL = "http://127.0.0.1:8000"
GRAPH_NAME = "sandbox-agent"
TIMEOUT = httpx.Timeout(connect=5, read=120, write=10, pool=10)


def _aegra_available() -> bool:
    try:
        r = httpx.get(BASE_URL, timeout=3)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _aegra_available(),
    reason="Aegra dev server is not running on localhost:8000",
)


# ── Fixtures ──────────────────────────────────────────────────


def _kill_sandbox_containers() -> None:
    """Remove all sandbox containers via Docker SDK (server-side cleanup)."""
    try:
        import docker

        client = docker.from_env()
        for c in client.containers.list(all=True, filters={"label": "sandbox-agent=true"}):
            try:
                c.stop(timeout=3)
                c.remove(force=True)
            except Exception:
                pass
    except Exception:
        pass


@pytest.fixture(scope="module")
def client():
    _kill_sandbox_containers()
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as c:
        yield c
    _kill_sandbox_containers()


@pytest.fixture()
def thread_id(client: httpx.Client):
    """Create a fresh thread and delete it after the test."""
    r = client.post("/threads", json={})
    assert r.status_code == 200
    tid = r.json()["thread_id"]
    yield tid
    client.delete(f"/threads/{tid}")


def _collect_stream_events(response: httpx.Response) -> list[dict]:
    """Parse an SSE stream into a list of {event, data} dicts."""
    events: list[dict] = []
    current_event = ""
    data_lines: list[str] = []

    for line in response.iter_lines():
        if line.startswith("event:"):
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line == "":
            if data_lines:
                raw = "\n".join(data_lines)
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = raw
                events.append({"event": current_event, "data": parsed})
            current_event = ""
            data_lines = []

    if data_lines:
        raw = "\n".join(data_lines)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        events.append({"event": current_event, "data": parsed})

    return events


def _run_and_wait(client: httpx.Client, thread_id: str, message: str) -> list[dict]:
    """Stream a run to completion and return all SSE events."""
    payload = {
        "assistant_id": GRAPH_NAME,
        "input": {"messages": [{"role": "human", "content": message}]},
        "stream_mode": "values",
    }
    with client.stream("POST", f"/threads/{thread_id}/runs/stream", json=payload) as r:
        assert r.status_code == 200
        return _collect_stream_events(r)


# ── Thread Management ─────────────────────────────────────────


class TestThreads:
    def test_create_thread(self, client: httpx.Client):
        r = client.post("/threads", json={})
        assert r.status_code == 200
        data = r.json()
        assert "thread_id" in data
        client.delete(f"/threads/{data['thread_id']}")

    def test_get_thread(self, client: httpx.Client, thread_id: str):
        r = client.get(f"/threads/{thread_id}")
        assert r.status_code == 200
        assert r.json()["thread_id"] == thread_id

    def test_delete_thread(self, client: httpx.Client):
        r = client.post("/threads", json={})
        tid = r.json()["thread_id"]
        r = client.delete(f"/threads/{tid}")
        assert r.status_code == 200

    def test_get_nonexistent_thread(self, client: httpx.Client):
        fake_id = uuid.uuid4().hex
        r = client.get(f"/threads/{fake_id}")
        assert r.status_code in (404, 422)


# ── Assistants ────────────────────────────────────────────────


class TestAssistants:
    def test_search_assistants(self, client: httpx.Client):
        r = client.post("/assistants/search", json={})
        assert r.status_code == 200
        assistants = r.json()
        assert isinstance(assistants, list)

    def test_create_assistant(self, client: httpx.Client):
        unique_name = f"test-{uuid.uuid4().hex[:8]}"
        r = client.post("/assistants", json={"graph_id": GRAPH_NAME, "name": unique_name})
        assert r.status_code in (200, 409)
        if r.status_code == 200:
            assert "assistant_id" in r.json()


# ── Agent Runs (requires LLM key) ────────────────────────────


needs_llm = pytest.mark.skipif(
    not os.environ.get("CHAT_MODEL_API_KEY", ""),
    reason="CHAT_MODEL_API_KEY not set",
)


def _docker_available() -> bool:
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:
        return False


needs_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker is not available",
)


@needs_llm
@needs_docker
class TestAgentRuns:
    """Full end-to-end tests that run the agent via the API.

    These create real containers and make real LLM calls.
    """

    def test_simple_python_execution(self, client: httpx.Client, thread_id: str):
        """Agent creates a session, runs code, and returns the result."""
        events = _run_and_wait(
            client, thread_id,
            "Print 'hello from API test' in Python, then stop the session.",
        )

        assert len(events) > 0

        values_events = [e for e in events if e["event"] == "values"]
        assert len(values_events) > 0

        last_values = values_events[-1]["data"]
        messages = last_values.get("messages", [])
        assert len(messages) >= 2

        final_msg = messages[-1]
        assert final_msg["type"] in ("ai", "AIMessage", "AIMessageChunk")
        assert final_msg.get("content")

    def test_conversation_persistence(self, client: httpx.Client, thread_id: str):
        """State persists across multiple runs on the same thread."""
        _run_and_wait(
            client,
            thread_id,
            "Create a Python session and run: x = 42; print(x). Keep the session active.",
        )

        events = _run_and_wait(
            client,
            thread_id,
            "In the same session, print x * 2. Then stop the session.",
        )

        values_events = [e for e in events if e["event"] == "values"]
        assert len(values_events) > 0

        last_messages = values_events[-1]["data"].get("messages", [])
        full_text = " ".join(
            m.get("content", "") for m in last_messages
            if isinstance(m.get("content"), str)
        )
        assert "84" in full_text

    def test_thread_state_after_run(self, client: httpx.Client, thread_id: str):
        """Verify thread state endpoint returns conversation history."""
        _run_and_wait(
            client, thread_id,
            "Say exactly: 'API state test OK'. Do NOT create any session.",
        )

        r = client.get(f"/threads/{thread_id}/state")
        assert r.status_code == 200

        state = r.json()
        assert "values" in state
        messages = state["values"].get("messages", [])
        assert len(messages) >= 2

    def test_multiple_threads_independent(self, client: httpx.Client):
        """Two threads have completely independent conversations."""
        _kill_sandbox_containers()

        r1 = client.post("/threads", json={})
        r2 = client.post("/threads", json={})
        tid1 = r1.json()["thread_id"]
        tid2 = r2.json()["thread_id"]

        try:
            _run_and_wait(
                client, tid1,
                "Create a Python session and run: secret = 'alpha'; print(secret). "
                "Keep the session active.",
            )
            _run_and_wait(
                client, tid2,
                "Create a Python session and run: secret = 'beta'; print(secret). "
                "Keep the session active.",
            )

            events1 = _run_and_wait(
                client, tid1,
                "Print the value of 'secret' using the same session. Then stop the session.",
            )
            events2 = _run_and_wait(
                client, tid2,
                "Print the value of 'secret' using the same session. Then stop the session.",
            )

            text1 = " ".join(
                m.get("content", "")
                for e in events1 if e["event"] == "values"
                for m in e["data"].get("messages", [])
                if isinstance(m.get("content"), str)
            )
            text2 = " ".join(
                m.get("content", "")
                for e in events2 if e["event"] == "values"
                for m in e["data"].get("messages", [])
                if isinstance(m.get("content"), str)
            )

            assert "alpha" in text1
            assert "beta" in text2
            assert "beta" not in text1
            assert "alpha" not in text2
        finally:
            _kill_sandbox_containers()
            client.delete(f"/threads/{tid1}")
            client.delete(f"/threads/{tid2}")


# ── API Error Handling ────────────────────────────────────────


class TestAPIErrors:
    def test_invalid_graph_id(self, client: httpx.Client, thread_id: str):
        payload = {
            "assistant_id": "nonexistent-graph",
            "input": {"messages": [{"role": "human", "content": "test"}]},
        }
        r = client.post(f"/threads/{thread_id}/runs", json=payload)
        assert r.status_code in (404, 422, 500)

    def test_empty_input(self, client: httpx.Client, thread_id: str):
        payload = {
            "assistant_id": GRAPH_NAME,
            "input": {},
        }
        r = client.post(f"/threads/{thread_id}/runs", json=payload)
        assert r.status_code in (200, 422)

    def test_missing_assistant_id(self, client: httpx.Client, thread_id: str):
        payload = {
            "input": {"messages": [{"role": "human", "content": "test"}]},
        }
        r = client.post(f"/threads/{thread_id}/runs", json=payload)
        assert r.status_code in (422, 400)
