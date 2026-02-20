"""
Debug test: traces every step of the LangGraph agent flow.
Runs a real query through the full pipeline and logs each message,
tool call, and tool result in detail.

Usage:
    sg docker -c "bash -c 'set -a; source .env; set +a; \
    uv run pytest tests/test_langgraph_debug.py -v -s'"
"""

from __future__ import annotations

import json
import os
from textwrap import indent

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools import create_tools


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _docker_available(), reason="Docker is not available"),
    pytest.mark.skipif(not os.environ.get("CHAT_MODEL_API_KEY", ""), reason="CHAT_MODEL_API_KEY not set"),
]

SEPARATOR = "─" * 80


def pp_json(obj: object) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(obj)


def dump_message(idx: int, msg) -> None:
    kind = type(msg).__name__
    print(f"\n{SEPARATOR}")
    print(f"  Message #{idx}  —  {kind}")
    print(SEPARATOR)

    if isinstance(msg, SystemMessage):
        print(f"  [SystemMessage] length={len(msg.content)} chars")
        print(indent(msg.content[:300] + ("..." if len(msg.content) > 300 else ""), "    "))

    elif isinstance(msg, HumanMessage):
        print(f"  [HumanMessage] content={msg.content!r}")

    elif isinstance(msg, AIMessage):
        if msg.content:
            print(f"  [AIMessage] content ({len(msg.content)} chars):")
            print(indent(msg.content[:500], "    "))

        tool_calls = getattr(msg, "tool_calls", None) or []
        if tool_calls:
            print(f"  [AIMessage] tool_calls ({len(tool_calls)}):")
            for j, tc in enumerate(tool_calls):
                print(f"    call[{j}] name={tc['name']!r}  id={tc.get('id', '?')!r}")
                print(f"    call[{j}] args={pp_json(tc.get('args', {}))}")
        else:
            print("  [AIMessage] no tool_calls (final answer)")

        if hasattr(msg, "response_metadata"):
            meta = msg.response_metadata or {}
            usage = meta.get("token_usage", {})
            if usage:
                print(
                    f"  [AIMessage] tokens: prompt={usage.get('prompt_tokens')}"
                    f"  completion={usage.get('completion_tokens')}"
                    f"  total={usage.get('total_tokens')}"
                )
            finish = meta.get("finish_reason", "?")
            print(f"  [AIMessage] finish_reason={finish}")

    elif isinstance(msg, ToolMessage):
        print(f"  [ToolMessage] name={msg.name!r}  tool_call_id={msg.tool_call_id!r}")
        content = msg.content
        print(f"  [ToolMessage] raw content ({len(content)} chars):")
        try:
            parsed = json.loads(content)
            pretty = pp_json(parsed)
            if len(pretty) > 1000:
                pretty = pretty[:1000] + "\n    ... (truncated)"
            print(indent(pretty, "    "))
        except (json.JSONDecodeError, TypeError):
            trimmed = content[:1000] + ("..." if len(content) > 1000 else "")
            print(indent(trimmed, "    "))

        if hasattr(msg, "status"):
            print(f"  [ToolMessage] status={msg.status!r}")

    else:
        print(f"  [{kind}] {str(msg)[:300]}")


class TestLangGraphDebug:
    """Run a simple query through the full agent and trace every step."""

    def test_full_agent_trace(self):
        """Runs the compiled graph via stream (same path as the CLI) and traces
        every message produced at each step."""
        from sandbox_agent.agent.graph import build_agent

        manager = SandboxManager()

        try:
            tools = create_tools(manager)

            print("\n\n" + "=" * 80)
            print("  TOOL SCHEMAS SENT TO THE LLM")
            print("=" * 80)
            for t in tools:
                schema = t.args_schema.model_json_schema()
                print(f"\n  Tool: {t.name}")
                print(f"  Description: {t.description[:120]}...")
                print(f"  Schema: {pp_json(schema)}")

            app = build_agent(manager=manager)

            print("\n\n" + "=" * 80)
            print("  STREAMING AGENT EXECUTION")
            print("=" * 80)

            input_msg = {"messages": [HumanMessage(content="Print 'hello sandbox' in Python")]}
            displayed_count = 0
            step = 0

            for state_snapshot in app.stream(
                input_msg,
                config={"recursion_limit": 20},
                stream_mode="values",
            ):
                all_msgs = state_snapshot.get("messages", [])
                new_msgs = all_msgs[displayed_count:]
                displayed_count = len(all_msgs)

                if new_msgs:
                    step += 1
                    print(f"\n\n{'*' * 80}")
                    print(f"  STREAM EVENT #{step} — {len(new_msgs)} new message(s)")
                    print(f"{'*' * 80}")

                    for msg in new_msgs:
                        idx = all_msgs.index(msg)
                        dump_message(idx, msg)

            print(f"\n\n{'=' * 80}")
            print(f"  FINAL STATE: {len(all_msgs)} messages, {step} stream events")
            print("=" * 80)
            for i, m in enumerate(all_msgs):
                kind = type(m).__name__
                preview = ""
                if isinstance(m, (HumanMessage, SystemMessage)):
                    preview = m.content[:60]
                elif isinstance(m, AIMessage):
                    preview = (m.content or "(tool_calls)")[:60]
                elif isinstance(m, ToolMessage):
                    preview = m.content[:60]
                print(f"  [{i}] {kind:20s} {preview}")

        finally:
            manager.cleanup_all()

    def test_tool_direct_invocation(self):
        """Test each tool individually with direct .invoke() calls."""
        manager = SandboxManager()

        try:
            tools = create_tools(manager)
            tool_map = {t.name: t for t in tools}

            print("\n\n" + "=" * 80)
            print("  DIRECT TOOL INVOCATION TEST")
            print("=" * 80)

            print("\n--- create_session ---")
            result_raw = tool_map["create_session"].invoke({"language": "python"})
            print(f"  raw type: {type(result_raw).__name__}")
            print(f"  raw result: {result_raw[:500]}")
            result = json.loads(result_raw)
            assert result.get("session_id"), f"No session_id: {result}"
            sid = result["session_id"]

            print("\n--- execute_code ---")
            result_raw = tool_map["execute_code"].invoke({
                "session_id": sid,
                "code": "x = 42\nprint(f'x = {x}')",
            })
            print(f"  result: {result_raw[:500]}")
            result = json.loads(result_raw)
            assert result["success"], f"Failed: {result}"

            print("\n--- execute_code (persistent state) ---")
            result_raw = tool_map["execute_code"].invoke({
                "session_id": sid,
                "code": "x * 2",
            })
            result = json.loads(result_raw)
            print(f"  result: {result.get('result')}")
            assert "84" in str(result.get("result", ""))

            print("\n--- execute_terminal ---")
            result_raw = tool_map["execute_terminal"].invoke({
                "session_id": sid,
                "command": "echo 'terminal works'",
            })
            result = json.loads(result_raw)
            print(f"  stdout: {result['stdout']!r}")
            assert result["exit_code"] == 0

            print("\n--- stop_session ---")
            result_raw = tool_map["stop_session"].invoke({"session_id": sid})
            result = json.loads(result_raw)
            print(f"  success: {result['success']}")
            assert result["success"]

            print("\n  All direct invocations passed!")

        finally:
            manager.cleanup_all()
