"""Interactive CLI for the Sandbox Agent.

Unified entry point with subcommands:
  sandbox-agent cli      — interactive CLI
  sandbox-agent mcp      — MCP server
  sandbox-agent api     — REST API (Aegra)
  sandbox-agent ui      — Streamlit UI (starts API if not running)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from sandbox_agent.agent.graph import build_agent
from sandbox_agent.clients import get_checkpointer
from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.settings import get_settings

logging.disable(logging.CRITICAL)

_MAX_TOOL_OUTPUT_LINES = 60


_RUNTIME_LEXER: dict[str, str] = {
    "python": "python",
    "node": "javascript",
    "r": "r",
    "julia": "julia",
}


def _format_tool_input(tool_call: dict[str, Any], manager: SandboxManager | None = None) -> Panel:
    name = tool_call.get("name", "?")
    args = tool_call.get("args", {})

    if name == "execute_code" and "code" in args:
        code = args["code"]
        lexer = "python"
        session_id = args.get("session_id", "")
        if manager and session_id:
            info = manager.sessions.get(session_id)
            if info:
                lexer = _RUNTIME_LEXER.get(info.runtime, "python")
        body = Syntax(code, lexer, theme="monokai", line_numbers=True, word_wrap=True)
    elif name == "execute_terminal" and "command" in args:
        body = Syntax(args["command"], "bash", theme="monokai", word_wrap=True)
    else:
        body = Text(json.dumps(args, indent=2, ensure_ascii=False, default=str))

    return Panel(
        body,
        title=f"[bold yellow]Tool Input[/bold yellow]  [dim]{name}[/dim]",
        border_style="yellow",
        padding=(0, 1),
    )


def _extract_text_content(content: Any) -> str:
    """Extract displayable text from a ToolMessage content (string or multimodal list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block["text"]
    return str(content)


def _format_tool_output(msg: ToolMessage) -> Panel:
    name = msg.name or "?"
    raw_content = msg.content
    text_content = _extract_text_content(raw_content)

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

    border = "red" if is_error else "green"
    status = "[red]ERRO" if is_error else "[green]OK"

    return Panel(
        Text(formatted),
        title=f"[bold {border}]Tool Output[/bold {border}]  [dim]{name}[/dim]  {status}",
        border_style=border,
        padding=(0, 1),
    )


def _api_is_healthy(url: str = "http://127.0.0.1:8000") -> bool:
    """Check if the Aegra API is reachable."""
    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/health")
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _run_api() -> None:
    """Run the Aegra API (aegra dev)."""
    subprocess.run(
        ["aegra", "dev"],
        cwd=Path.cwd(),
    )
    sys.exit(0)


def _run_ui(start_api_if_needed: bool = True) -> None:
    """Run the Streamlit frontend. Starts API in background if not running."""
    api_url = "http://127.0.0.1:8000"
    if _api_is_healthy(api_url):
        pass  # API already running
    elif start_api_if_needed:
        proc = subprocess.Popen(
            ["aegra", "dev"],
            cwd=Path.cwd(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        console = __import__("rich.console", fromlist=["Console"]).Console()
        console.print("[dim]API not detected. Starting in background...[/dim]")
        for _ in range(30):
            time.sleep(1)
            if _api_is_healthy(api_url):
                console.print("[green]API pronta em http://localhost:8000[/green]")
                break
        else:
            console.print(
                "[yellow]Timeout waiting for API. The frontend may fail to connect.[/yellow]"
            )
    else:
        console = __import__("rich.console", fromlist=["Console"]).Console()
        console.print(
            "[red]API is not running. Execute [bold]uv run sandbox-agent api[/bold] first.[/red]"
        )
        sys.exit(1)

    root = Path(__file__).resolve().parent.parent.parent
    app_path = root / "frontend" / "app.py"
    if not app_path.exists():
        print(f"Error: frontend app not found at {app_path}", file=sys.stderr)
        sys.exit(1)
    sys.argv = ["streamlit", "run", str(app_path), "--server.port=8501"]
    try:
        import streamlit.web.cli as stcli
    except ImportError:
        console = __import__("rich.console", fromlist=["Console"]).Console()
        console.print(
            "[red]Frontend dependencies not installed.[/red]\n"
            "Execute: [cyan]uv sync --extra frontend[/cyan]"
        )
        sys.exit(1)

    stcli.main()


def run_frontend_entry() -> None:
    """Entry point for sandbox-agent-frontend (backward compat, with auto-start API)."""
    _run_ui()


def _print_help() -> None:
    console = __import__("rich.console", fromlist=["Console"]).Console()
    console.print(
        "[bold]sandbox-agent[/bold] — LangGraph agent with sandboxed execution\n"
        "\n[cyan]Uso:[/cyan]\n"
        "  uv run sandbox-agent [command]\n"
        "\n[cyan]Commands:[/cyan]\n"
        "  cli       — Interactive CLI (Rich REPL)\n"
        "  mcp       — MCP server (Cursor, Claude Desktop)\n"
        "  api       — REST API (Aegra)\n"
        "  ui        — Streamlit UI (starts API automatically if needed)\n"
    )


def run_interactive_cli() -> None:
    """Run the interactive CLI (Rich REPL)."""
    console = Console()

    settings = get_settings()
    if not settings.CHAT_MODEL_API_KEY:
        console.print(
            Panel(
                "[bold red]CHAT_MODEL_API_KEY not configured.[/bold red]\n\n"
                "Configure via environment variable or .env file.\n"
                "See .env.example for reference.",
                title="Configuration Error",
                border_style="red",
            )
        )
        sys.exit(1)

    checkpointer = get_checkpointer()

    try:
        manager = SandboxManager()
    except Exception as exc:
        msg = str(exc)
        if "Permission denied" in msg or "PermissionError" in msg:
            console.print(
                Panel(
                    "[bold red]No permission to access Docker.[/bold red]\n\n"
                    "Add your user to the docker group:\n"
                    "  [cyan]sudo usermod -aG docker $USER[/cyan]\n\n"
                    "Then logout/login or run in the current terminal:\n"
                    "  [cyan]newgrp docker[/cyan]",
                    title="Permission Error",
                    border_style="red",
                )
            )
        else:
            console.print(
                Panel(
                    f"[bold red]Error connecting to Docker:[/bold red]\n\n{msg}\n\n"
                    "Check if Docker is installed and running:\n"
                    "  [cyan]sudo systemctl start docker[/cyan]",
                    title="Docker Error",
                    border_style="red",
                )
            )
        sys.exit(1)

    app = build_agent(manager=manager, checkpointer=checkpointer)

    console.print()
    console.print(
        Panel(
            "[bold]Sandbox Agent[/bold]\n\n"
            f"Model: [cyan]{settings.CHAT_MODEL}[/cyan]\n"
            f"Memory limit: [cyan]{settings.CONTAINER_MEMORY_LIMIT}[/cyan]\n"
            f"Timeout: [cyan]{settings.EXECUTION_TIMEOUT_SECONDS}s[/cyan] | "
            f"Max sessions: [cyan]{settings.MAX_SESSIONS}[/cyan]\n\n"
            "[dim]Enter your question or command. Use 'exit' to end.[/dim]",
            title="Welcome",
            border_style="blue",
        )
    )
    console.print()

    try:
        while True:
            try:
                user_input = console.input("[bold green]You:[/bold green] ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Ending...[/dim]")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "q"):
                console.print("[dim]Goodbye![/dim]")
                break

            new_msg = HumanMessage(content=user_input)

            console.print()

            final_ai_content = ""

            try:
                config: dict[str, Any] = {
                    "recursion_limit": settings.MAX_ITERATIONS,
                    "configurable": {"thread_id": "cli"},
                }
                input_messages = [new_msg]
                state_before = app.get_state(config)
                displayed_count = len(state_before.values.get("messages", []))

                for state_snapshot in app.stream(
                    {"messages": input_messages},
                    config=config,
                    stream_mode="values",
                ):
                    all_msgs = state_snapshot.get("messages", [])
                    new_msgs = all_msgs[displayed_count:]
                    displayed_count = len(all_msgs)

                    for msg in new_msgs:
                        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                            for tc in msg.tool_calls:
                                console.print(_format_tool_input(tc, manager))

                        if isinstance(msg, ToolMessage):
                            console.print(_format_tool_output(msg))
                            console.print()

                        if isinstance(msg, AIMessage) and msg.content:
                            final_ai_content = msg.content

            except Exception as exc:
                console.print(
                    Panel(
                        f"[red]{type(exc).__name__}: {exc}[/red]",
                        title="Error",
                        border_style="red",
                    )
                )
                console.print()
                continue

            if final_ai_content:
                try:
                    console.print(
                        Panel(
                            Markdown(final_ai_content),
                            title="Agent",
                            border_style="blue",
                        )
                    )
                except Exception:
                    console.print(
                        Panel(
                            Text(final_ai_content),
                            title="Agent",
                            border_style="blue",
                        )
                    )
            console.print()
    finally:
        console.print("[dim]Cleaning up containers...[/dim]")
        manager.cleanup_all()


def main() -> None:
    """Entry point — dispatches to subcommands."""
    args = sys.argv[1:]
    cmd = args[0] if args else None

    if cmd == "cli" or cmd is None:
        run_interactive_cli()
    elif cmd == "mcp":
        from sandbox_agent.mcp_server import main as mcp_main

        mcp_main()
    elif cmd == "api":
        _run_api()
    elif cmd == "ui":
        _run_ui()
    elif cmd in ("-h", "--help", "help"):
        _print_help()
    else:
        print(f"Comando desconhecido: {cmd}", file=sys.stderr)
        _print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
