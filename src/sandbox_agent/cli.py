"""Interactive CLI for the Sandbox Agent."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from sandbox_agent.agent.graph import build_agent
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


def main() -> None:
    """Entry point for the ``sandbox-agent`` CLI command."""
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

    app = build_agent(manager=manager)

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

    messages: list = []

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

            messages.append(HumanMessage(content=user_input))
            console.print()

            final_ai_content = ""

            try:
                displayed_count = len(messages)

                for state_snapshot in app.stream(
                    {"messages": messages},
                    config={"recursion_limit": settings.MAX_ITERATIONS},
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

                messages = all_msgs  # noqa: F821

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


if __name__ == "__main__":
    main()
