"""Interactive CLI for the Sandbox Agent.

Unified entry point with subcommands:
  sandbox-agent cli      — interactive CLI (API client with Rich rendering)
  sandbox-agent mcp      — MCP server
  sandbox-agent api      — REST API (Aegra)
  sandbox-agent ui       — React UI (requires API running)

The CLI operates as a thin client on top of the Aegra API — the same
API that the React frontend uses.  This ensures that export URLs,
thread persistence, and all other features work identically everywhere.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

_API_PROCESS: subprocess.Popen | None = None

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from sandbox_agent.settings import get_settings

for _logger_name in ("sandbox_agent", "httpx", "docker", "langchain", "langgraph"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)

_console = Console()

# ── Persistent CLI state ────────────────────────────────────────────────

_CLI_STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
) / "sandbox-agent"


def _load_cli_thread_id() -> str | None:
    path = _CLI_STATE_DIR / "cli-thread.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("thread_id")
    except Exception:
        return None


def _save_cli_thread_id(thread_id: str) -> None:
    _CLI_STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CLI_STATE_DIR / "cli-thread.json"
    path.write_text(json.dumps({"thread_id": thread_id}), encoding="utf-8")


# ── Session tracking (for syntax highlighting) ─────────────────────────

_RUNTIME_LANGUAGE: dict[str, str] = {
    "python": "python",
    "node": "javascript",
    "r": "r",
}


def _track_sessions(messages: list[dict], sessions: dict[str, str]) -> None:
    """Update *sessions* (session_id -> runtime) from tool messages."""
    for msg in messages:
        if msg.get("type") not in ("tool", "ToolMessage"):
            continue
        name = msg.get("name", "")
        if name != "create_session":
            continue
        content = msg.get("content", "")
        try:
            data = json.loads(content) if isinstance(content, str) else content
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("session_id"):
            sessions[data["session_id"]] = data.get("runtime", "python")


# ── Rich formatting ─────────────────────────────────────────────────────

MAX_TOOL_OUTPUT_LINES = 60


def _format_tool_input(tool_call: dict[str, Any], sessions: dict[str, str]) -> Panel:
    name = tool_call.get("name", "?")
    args = tool_call.get("args", {})

    if name == "execute_code" and "code" in args:
        code = args["code"]
        session_id = args.get("session_id", "")
        lexer = _RUNTIME_LANGUAGE.get(sessions.get(session_id, ""), "python")
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
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block["text"]
    return str(content)


def _format_tool_output(msg: dict) -> Panel:
    name = msg.get("name", "?")
    raw_content = msg.get("content", "")
    text_content = _extract_text_content(raw_content)

    parsed = None
    try:
        parsed = json.loads(text_content) if isinstance(text_content, str) else text_content
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False, default=str)
    except (json.JSONDecodeError, TypeError):
        formatted = str(text_content)

    lines = formatted.splitlines()
    if len(lines) > MAX_TOOL_OUTPUT_LINES:
        visible = lines[:MAX_TOOL_OUTPUT_LINES]
        omitted = len(lines) - MAX_TOOL_OUTPUT_LINES
        visible.append(f"\n... +{omitted} linhas omitidas ...")
        formatted = "\n".join(visible)

    is_error = isinstance(parsed, dict) and parsed.get("success") is False
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


# ── API lifecycle ───────────────────────────────────────────────────────


def _api_is_healthy(url: str = "http://127.0.0.1:8000") -> bool:
    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/health")
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _ensure_api_running(console: Console) -> bool:
    """Check if the Aegra API is running. Returns True when healthy."""
    api_url = get_settings().API_BASE_URL
    if _api_is_healthy(api_url):
        return True

    console.print(
        "[red]API is not running.[/red]\n"
        "Start manually with: [cyan]uv run sandbox-agent api[/cyan]"
    )
    return False


def _postgres_reachable(host: str, port: str) -> bool:
    """Check if PostgreSQL is accepting connections."""
    try:
        port_int = int(port)
        with socket.create_connection((host, port_int), timeout=2):
            return True
    except (socket.error, ValueError):
        return False


def _ensure_postgres_running(console: Console) -> bool:
    """Start PostgreSQL via Docker if not reachable. Returns True when ready."""
    settings = get_settings()
    host = settings.POSTGRES_HOST
    port = settings.POSTGRES_PORT

    if _postgres_reachable(host, port):
        return True

    if host not in ("localhost", "127.0.0.1"):
        console.print(
            "[yellow]PostgreSQL not reachable at %s:%s.[/yellow]\n"
            "Start your database manually or set POSTGRES_HOST=localhost to use Docker."
            % (host, port)
        )
        return False

    compose = Path.cwd() / "docker-compose.yml"
    if not compose.exists():
        console.print(
            "[red]PostgreSQL not running and docker-compose.yml not found.[/red]\n"
            "Run [cyan]aegra dev[/cyan] or start PostgreSQL manually."
        )
        return False

    console.print("[dim]Starting PostgreSQL via Docker Compose...[/dim]")
    result = subprocess.run(
        ["docker", "compose", "up", "postgres", "-d"],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(
            "[red]Failed to start PostgreSQL.[/red]\n"
            "Ensure Docker is running. Stderr: %s" % (result.stderr or result.stdout or "")
        )
        return False

    # Poll until reachable (--wait may not exist in older compose)
    for _ in range(30):
        if _postgres_reachable(host, port):
            console.print("[green]PostgreSQL ready.[/green]")
            return True
        time.sleep(1)

    console.print("[red]PostgreSQL did not become ready in time.[/red]")
    return False


def _run_api(dev: bool = False) -> None:
    """Run the Aegra API. Use dev=True for hot reload."""
    if dev:
        cmd = ["aegra", "dev"]
    else:
        if not _ensure_postgres_running(_console):
            sys.exit(1)
        cmd = ["aegra", "serve", "--host", "0.0.0.0", "--port", "8000"]
    subprocess.run(cmd, cwd=Path.cwd())
    sys.exit(0)


def _kill_api_if_started_by_ui() -> None:
    """Kill the API process if we started it for the UI."""
    global _API_PROCESS
    if _API_PROCESS is not None and _API_PROCESS.poll() is None:
        _API_PROCESS.terminate()
        try:
            _API_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _API_PROCESS.kill()
        _API_PROCESS = None


def _run_ui() -> None:
    """Run the React frontend. Auto-starts API in background if not running."""
    global _API_PROCESS
    api_url = get_settings().API_BASE_URL

    if not _api_is_healthy(api_url):
        _console.print("[dim]API not running. Starting in background...[/dim]")
        if not _ensure_postgres_running(_console):
            sys.exit(1)
        _API_PROCESS = subprocess.Popen(
            ["aegra", "serve", "--host", "0.0.0.0", "--port", "8000"],
            cwd=Path.cwd(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        atexit.register(_kill_api_if_started_by_ui)
        for i in range(30):
            if _api_is_healthy(api_url):
                _console.print("[green]API ready.[/green]")
                break
            time.sleep(1)
        else:
            _kill_api_if_started_by_ui()
            _console.print(
                "[red]API did not become ready in time.[/red]\n"
                "Start manually with: [cyan]uv run sandbox-agent api[/cyan]"
            )
            sys.exit(1)

    root = Path(__file__).resolve().parent.parent.parent
    frontend_dir = root / "frontend"
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        _console.print(
            "[red]Frontend not found.[/red]\n"
            f"Expected: {frontend_dir}"
        )
        sys.exit(1)

    node_modules = frontend_dir / "node_modules"
    if not node_modules.exists():
        _console.print("[yellow]Installing frontend dependencies...[/yellow]")
        subprocess.run(
            ["npm", "install"],
            cwd=frontend_dir,
            check=True,
        )

    _console.print(
        "[green]Starting frontend at http://localhost:5173[/green]\n"
        f"API: [cyan]{api_url}[/cyan]"
    )
    try:
        subprocess.run(
            ["npm", "run", "dev"],
            cwd=frontend_dir,
        )
    finally:
        _kill_api_if_started_by_ui()


def run_frontend_entry() -> None:
    """Entry point for sandbox-agent-frontend (backward compat)."""
    _run_ui()


def _print_help() -> None:
    _console.print(
        "[bold]sandbox-agent[/bold] — LangGraph agent with sandboxed execution\n"
        "\n[cyan]Uso:[/cyan]\n"
        "  uv run sandbox-agent [command]\n"
        "\n[cyan]Commands:[/cyan]\n"
        "  cli         — Interactive CLI (Rich REPL, via API)\n"
        "  mcp         — MCP server (Cursor, Claude Desktop)\n"
        "  api         — REST API (Aegra, sem reload)\n"
        "  api dev     — REST API com hot reload\n"
        "  ui          — React UI (requires API running)\n"
    )


# ── Interactive CLI (API client) ────────────────────────────────────────


def run_interactive_cli() -> None:
    """Run the interactive CLI as a thin client on top of the Aegra API."""
    from sandbox_agent.clients import AegraClient

    console = Console()
    settings = get_settings()

    # Ensure API is running (auto-start if needed)
    if not _ensure_api_running(console):
        console.print(
            Panel(
                "[bold red]Cannot start or reach the API.[/bold red]\n\n"
                "Start manually with: [cyan]uv run sandbox-agent api[/cyan]",
                title="API Error",
                border_style="red",
            )
        )
        sys.exit(1)

    client = AegraClient(base_url=settings.API_BASE_URL)

    # Thread management: resume last CLI thread or create new
    thread_id = _load_cli_thread_id()
    if thread_id:
        try:
            client.get_thread(thread_id)
        except Exception:
            thread_id = None

    if not thread_id:
        try:
            thread = client.create_thread(metadata={"source": "cli"})
            thread_id = thread["thread_id"]
            _save_cli_thread_id(thread_id)
        except Exception as exc:
            console.print(
                Panel(
                    f"[bold red]Failed to create thread:[/bold red]\n\n{exc}",
                    title="API Error",
                    border_style="red",
                )
            )
            sys.exit(1)

    # Track sessions for syntax highlighting
    sessions: dict[str, str] = {}

    # Load existing messages to populate session tracker
    try:
        state = client.get_thread_state(thread_id)
        existing_msgs = state.get("values", {}).get("messages", [])
        _track_sessions(existing_msgs, sessions)
    except Exception:
        existing_msgs = []

    console.print()
    console.print(
        Panel(
            "[bold]Sandbox Agent[/bold]  [dim](API mode)[/dim]\n\n"
            f"Model: [cyan]{settings.CHAT_MODEL}[/cyan]\n"
            f"API: [cyan]{settings.API_BASE_URL}[/cyan]\n"
            f"Thread: [dim]{thread_id}[/dim]\n\n"
            "[dim]Enter your question or command. Use 'exit' to end.\n"
            "Use '/new' to start a new conversation.[/dim]",
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

            if user_input.lower() == "/new":
                try:
                    thread = client.create_thread(metadata={"source": "cli"})
                    thread_id = thread["thread_id"]
                    _save_cli_thread_id(thread_id)
                    sessions.clear()
                    console.print(
                        f"[green]New conversation:[/green] [dim]{thread_id}[/dim]\n"
                    )
                except Exception as exc:
                    console.print(f"[red]Failed to create thread: {exc}[/red]\n")
                continue

            console.print()

            input_messages = [{"role": "human", "content": user_input}]
            final_ai_content = ""
            displayed_count = 0

            try:
                for event in client.stream_run(
                    thread_id=thread_id,
                    input_messages=input_messages,
                    configurable={
                        "chat_model": settings.CHAT_MODEL,
                        "chat_model_provider": settings.CHAT_MODEL_PROVIDER,
                        "chat_model_api_key": settings.CHAT_MODEL_API_KEY,
                    },
                ):
                    if event.event != "values" or not isinstance(event.data, dict):
                        continue

                    all_msgs = event.data.get("messages", [])
                    new_msgs = all_msgs[displayed_count:]
                    displayed_count = len(all_msgs)

                    _track_sessions(new_msgs, sessions)

                    for msg in new_msgs:
                        msg_type = msg.get("type", "")

                        if msg_type in ("ai", "AIMessage", "AIMessageChunk"):
                            tool_calls = msg.get("tool_calls", [])
                            if tool_calls:
                                for tc in tool_calls:
                                    console.print(_format_tool_input(tc, sessions))

                            content = msg.get("content", "")
                            if content and not tool_calls:
                                if isinstance(content, str):
                                    final_ai_content = content
                                else:
                                    final_ai_content = _extract_text_content(content)

                        elif msg_type in ("tool", "ToolMessage"):
                            console.print(_format_tool_output(msg))
                            console.print()

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
        client.close()


# ── Entry point ─────────────────────────────────────────────────────────


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
        _run_api(dev=(len(args) > 1 and args[1] == "dev"))
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
