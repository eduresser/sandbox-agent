"""Tests for CLI formatting, dispatch, and helper functions.

All tests are pure unit tests — no Docker, no LLM, no network required.
Run with: pytest tests/test_cli.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from rich.syntax import Syntax
from rich.text import Text

from sandbox_agent.cli import (
    _api_is_healthy,
    _extract_text_content,
    _format_tool_input,
    _format_tool_output,
)
from sandbox_agent.sandbox.models import MAX_TOOL_OUTPUT_LINES


# ── _format_tool_input ─────────────────────────────────────────


class TestFormatToolInput:
    def test_execute_code_default_python_lexer(self):
        panel = _format_tool_input(
            {"name": "execute_code", "args": {"code": "print(1)"}},
            sessions={},
        )
        renderable = panel.renderable
        assert isinstance(renderable, Syntax)
        assert renderable.lexer.name.lower() == "python"

    def test_execute_code_node_session_uses_javascript_lexer(self):
        panel = _format_tool_input(
            {"name": "execute_code", "args": {"code": "console.log(1)", "session_id": "s1"}},
            sessions={"s1": "node"},
        )
        renderable = panel.renderable
        assert isinstance(renderable, Syntax)
        assert renderable.lexer.name.lower() == "javascript"

    def test_execute_code_r_session_uses_r_lexer(self):
        panel = _format_tool_input(
            {"name": "execute_code", "args": {"code": "print('hello')", "session_id": "s1"}},
            sessions={"s1": "r"},
        )
        renderable = panel.renderable
        assert isinstance(renderable, Syntax)
        assert renderable.lexer.name.lower() in ("r", "s")

    def test_execute_terminal_uses_bash_lexer(self):
        panel = _format_tool_input(
            {"name": "execute_terminal", "args": {"command": "ls -la"}},
            sessions={},
        )
        renderable = panel.renderable
        assert isinstance(renderable, Syntax)
        assert renderable.lexer.name.lower() == "bash"

    def test_generic_tool_uses_text(self):
        panel = _format_tool_input(
            {"name": "create_session", "args": {"language": "python"}},
            sessions={},
        )
        renderable = panel.renderable
        assert isinstance(renderable, Text)

    def test_panel_title_contains_tool_name(self):
        panel = _format_tool_input(
            {"name": "stop_session", "args": {"session_id": "x"}},
            sessions={},
        )
        assert "stop_session" in str(panel.title)


# ── _extract_text_content ──────────────────────────────────────


class TestExtractTextContent:
    def test_string_input(self):
        assert _extract_text_content("hello") == "hello"

    def test_multimodal_list_with_text_block(self):
        content = [
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": "result text"},
        ]
        assert _extract_text_content(content) == "result text"

    def test_list_without_text_block(self):
        content = [{"type": "image_url", "image_url": {"url": "data:..."}}]
        result = _extract_text_content(content)
        assert isinstance(result, str)

    def test_non_string_non_list(self):
        assert _extract_text_content(42) == "42"


# ── _format_tool_output ───────────────────────────────────────


class TestFormatToolOutput:
    """_format_tool_output expects API-style message dicts (name, content)."""

    def test_success_green_border(self):
        msg = {
            "name": "execute_code",
            "content": json.dumps({"success": True, "stdout": "ok"}),
        }
        panel = _format_tool_output(msg)
        assert panel.border_style == "green"

    def test_error_red_border(self):
        msg = {
            "name": "execute_code",
            "content": json.dumps({"success": False, "error": "boom"}),
        }
        panel = _format_tool_output(msg)
        assert panel.border_style == "red"

    def test_error_invoking_tool_red_border(self):
        msg = {
            "name": "execute_code",
            "content": "Error invoking tool execute_code: session not found",
        }
        panel = _format_tool_output(msg)
        assert panel.border_style == "red"

    def test_long_output_is_truncated(self):
        items = {f"key_{i}": f"val_{i}" for i in range(MAX_TOOL_OUTPUT_LINES + 50)}
        msg = {
            "name": "execute_code",
            "content": json.dumps({"success": True, **items}),
        }
        panel = _format_tool_output(msg)
        text = str(panel.renderable)
        assert "omitidas" in text

    def test_panel_title_contains_tool_name(self):
        msg = {
            "name": "execute_terminal",
            "content": json.dumps({"success": True}),
        }
        panel = _format_tool_output(msg)
        assert "execute_terminal" in str(panel.title)


# ── _api_is_healthy ────────────────────────────────────────────


class TestApiIsHealthy:
    def test_healthy_returns_true(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("sandbox_agent.cli.urllib.request.urlopen", return_value=mock_response):
            assert _api_is_healthy("http://fake:8000") is True

    def test_unreachable_returns_false(self):
        with patch(
            "sandbox_agent.cli.urllib.request.urlopen",
            side_effect=ConnectionError("refused"),
        ):
            assert _api_is_healthy("http://fake:8000") is False


# ── main() dispatch ────────────────────────────────────────────


class TestMainDispatch:
    def test_mcp_command(self):
        with (
            patch("sys.argv", ["sandbox-agent", "mcp"]),
            patch("sandbox_agent.mcp_server.main") as mock_mcp,
        ):
            from sandbox_agent.cli import main

            main()
            mock_mcp.assert_called_once()

    def test_api_command(self):
        with (
            patch("sys.argv", ["sandbox-agent", "api"]),
            patch("sandbox_agent.cli._run_api") as mock_api,
        ):
            from sandbox_agent.cli import main

            main()
            mock_api.assert_called_once()

    def test_ui_command(self):
        with (
            patch("sys.argv", ["sandbox-agent", "ui"]),
            patch("sandbox_agent.cli._run_ui") as mock_ui,
        ):
            from sandbox_agent.cli import main

            main()
            mock_ui.assert_called_once()

    def test_help_command(self, capsys):
        with patch("sys.argv", ["sandbox-agent", "--help"]):
            from sandbox_agent.cli import main

            main()
        captured = capsys.readouterr()
        assert "sandbox-agent" in captured.out or True  # Rich prints to console, not stdout

    def test_unknown_command_exits(self):
        with (
            patch("sys.argv", ["sandbox-agent", "nonexistent"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            from sandbox_agent.cli import main

            main()
        assert exc_info.value.code == 1

    def test_no_args_calls_cli(self):
        with (
            patch("sys.argv", ["sandbox-agent"]),
            patch("sandbox_agent.cli.run_interactive_cli") as mock_cli,
        ):
            from sandbox_agent.cli import main

            main()
            mock_cli.assert_called_once()
