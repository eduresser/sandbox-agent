"""Streamlit frontend for the Sandbox Agent (Aegra API)."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from api_client import AegraClient
from utils import (
    ParsedToolResult,
    build_user_content,
    collect_tool_blocks,
    decode_b64_image,
    extract_sessions_from_messages,
    format_file_size,
    format_tool_input_display,
    format_tool_output_display,
    get_file_icon,
    parse_tool_message,
    save_uploaded_files,
    time_ago,
)

# ── Page Config ─────────────────────────────────────────

st.set_page_config(
    page_title="Sandbox Agent",
    page_icon="\U0001f4bb",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    /* Sidebar: pinned top/bottom, scrollable thread list */
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
        overflow: hidden !important;
        display: flex !important;
        flex-direction: column !important;
    }
    section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
        flex: 1 !important;
        display: flex !important;
        flex-direction: column !important;
        min-height: 0 !important;
        overflow: hidden !important;
        padding-bottom: 1rem !important;
    }
    section[data-testid="stSidebar"]
        [data-testid="stSidebarUserContent"] > div {
        flex: 1 !important;
        display: flex !important;
        flex-direction: column !important;
        min-height: 0 !important;
    }
    /* Outer vertical block only (direct child chain) */
    section[data-testid="stSidebar"]
        [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
        flex: 1 !important;
        display: flex !important;
        flex-direction: column !important;
        min-height: 0 !important;
    }
    section[data-testid="stSidebar"]
        [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] > * {
        flex-shrink: 0;
    }
    /* Thread list wrapper: fill remaining space */
    section[data-testid="stSidebar"] [data-testid="stLayoutWrapper"] {
        flex: 1 1 0 !important;
        min-height: 0 !important;
        overflow: hidden !important;
    }
    /* Inner scrollable area: constrain to wrapper height */
    section[data-testid="stSidebar"] [data-testid="stLayoutWrapper"] > div:first-child {
        height: 100% !important;
        max-height: 100% !important;
    }
    section[data-testid="stSidebar"]
        [data-testid="stLayoutWrapper"] [data-testid="stVerticalBlock"] {
        flex: unset !important;
        display: block !important;
    }
    /* Tool block: unified input+output */
    .tool-block {
        border-radius: 0.5rem;
        padding: 0.75rem 1rem;
        margin: 0.5rem 0;
        font-family: ui-monospace, monospace;
        font-size: 0.875rem;
    }
    .tool-block-input { margin-bottom: 0.75rem; }
    .tool-block-output { margin-top: 0.5rem; }
    .tool-block-title { font-weight: 600; margin-bottom: 0.5rem; }
    /* Download button: green when available (not disabled) */
    div[data-testid="stDownloadButton"] button:not(:disabled) {
        background-color: #28a745 !important;
        border-color: #28a745 !important;
        color: white !important;
    }
    div[data-testid="stDownloadButton"] button:not(:disabled):hover {
        background-color: #218838 !important;
        border-color: #1e7e34 !important;
        color: white !important;
    }
    /* Disabled button: gray */
    div[data-testid="stDownloadButton"] button:disabled,
    div[data-testid="stButton"] button:disabled {
        background-color: #6c757d !important;
        border-color: #6c757d !important;
        color: white !important;
        opacity: 0.65;
        cursor: not-allowed !important;
    }
    /* Button row + caption: flex, each item with its natural size */
    div[data-testid="stHorizontalBlock"]:has([data-testid="stDownloadButton"]) {
        display: flex !important;
        flex-wrap: wrap !important;
        align-items: center !important;
        gap: 0.5rem !important;
    }
    div[data-testid="stHorizontalBlock"]:has([data-testid="stDownloadButton"]) > div {
        flex: 0 0 auto !important;
        width: fit-content !important;
        max-width: fit-content !important;
    }
    /* Trash button in sidebar: fixed size, always on the right */
    section[data-testid="stSidebar"] [data-testid="stLayoutWrapper"] [data-testid="stHorizontalBlock"] {
        flex-direction: row !important;
        display: flex !important;
    }
    section[data-testid="stSidebar"] [data-testid="stLayoutWrapper"] [data-testid="stHorizontalBlock"] > div:first-child {
        flex: 1 !important;
        min-width: 0 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stLayoutWrapper"] [data-testid="stHorizontalBlock"] > div:last-child {
        min-width: 2.5rem !important;
        flex: 0 0 2.5rem !important;
        margin-left: auto !important;
    }
    section[data-testid="stSidebar"] [data-testid="stLayoutWrapper"] [data-testid="stHorizontalBlock"] > div:last-child [data-testid="stVerticalBlock"] {
        width: 2.5rem !important;
        min-width: 2.5rem !important;
    }
    section[data-testid="stSidebar"] [data-testid="stLayoutWrapper"] [data-testid="stHorizontalBlock"] > div:last-child button {
        width: 2.5rem !important;
        min-width: 2.5rem !important;
        height: 2.5rem !important;
        min-height: 2.5rem !important;
        padding: 0 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        aspect-ratio: 1 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Session State Defaults ──────────────────────────────

_DEFAULTS: dict = {
    "messages": [],
    "thread_id": None,
    "threads_list": [],
    "api_healthy": False,
    "chat_model": "gpt-4o",
    "chat_model_provider": "openai",
    "chat_model_api_key": "",
    "chat_model_base_url": "",
    "chat_model_supports_vision": True,
    "running": False,
    "uploaded_file_metas": [],
    "thread_previews": {},
}

for key, default in _DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ── API Client ──────────────────────────────────────────


@st.cache_resource
def get_client() -> AegraClient:
    return AegraClient()


client = get_client()

try:
    st.session_state.api_healthy = client.is_healthy()
except Exception:
    st.session_state.api_healthy = False

# ── Helpers ─────────────────────────────────────────────


def _get_configurable() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if st.session_state.chat_model:
        cfg["chat_model"] = st.session_state.chat_model
    if st.session_state.chat_model_provider:
        cfg["chat_model_provider"] = st.session_state.chat_model_provider
    if st.session_state.chat_model_api_key:
        cfg["chat_model_api_key"] = st.session_state.chat_model_api_key
    if st.session_state.chat_model_base_url:
        cfg["chat_model_base_url"] = st.session_state.chat_model_base_url
    return cfg


def _refresh_threads() -> None:
    try:
        st.session_state.threads_list = client.list_threads(limit=50)
    except Exception:
        st.session_state.threads_list = []


def _load_thread_messages(thread_id: str) -> list[dict]:
    """Load messages from a thread's persisted state."""
    try:
        state = client.get_thread_state(thread_id)
        values = state.get("values", {})
        return values.get("messages", [])
    except Exception:
        return []


def _create_new_thread() -> str | None:
    try:
        thread = client.create_thread()
        tid = thread["thread_id"]
        st.session_state.thread_id = tid
        st.session_state.messages = []
        st.session_state.uploaded_file_metas = []
        _refresh_threads()
        return tid
    except Exception as e:
        st.error(f"Error creating thread: {e}")
        return None


def _switch_thread(thread_id: str) -> None:
    st.session_state.thread_id = thread_id
    st.session_state.messages = _load_thread_messages(thread_id)
    st.session_state.uploaded_file_metas = []


def _get_thread_preview(thread_id: str) -> str:
    """Return a short preview of the first human message in a thread.

    Returns empty string if thread has no messages or no response (only human,
    no AI/tool reply) — such threads are not shown in history.
    """
    previews = st.session_state.thread_previews
    if thread_id in previews:
        return previews[thread_id]

    try:
        state = client.get_thread_state(thread_id)
        msgs = state.get("values", {}).get("messages", [])
        has_response = any(
            m.get("type") in ("ai", "AIMessage", "AIMessageChunk", "tool", "ToolMessage")
            for m in msgs
        )
        if not has_response:
            return ""

        for m in msgs:
            if m.get("type") in ("human", "HumanMessage"):
                content = m.get("content", "")
                if isinstance(content, list):
                    content = next(
                        (
                            b.get("text", "")
                            for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ),
                        "",
                    )
                text = str(content).strip().split("\n")[0]
                preview = text[:40] + ("..." if len(text) > 40 else "")
                previews[thread_id] = preview
                return preview
    except Exception:
        pass

    return ""


_PROVIDERS = ["openai", "anthropic", "google_genai", "azure_openai", "ollama", "fireworks"]


@st.dialog("Settings")
def _settings_dialog() -> None:
    # Health check
    try:
        healthy = client.is_healthy()
    except Exception:
        healthy = False

    if healthy:
        st.success("API Connected", icon="\u2705")
    else:
        st.error("API Unavailable", icon="\u274c")
    st.session_state.api_healthy = healthy

    st.subheader("LLM Model")
    new_model = st.text_input(
        "Model",
        value=st.session_state.chat_model,
        key="dlg_chat_model",
        placeholder="gpt-4o, claude-sonnet-4-20250514, gemini-2.0-flash...",
    )
    cur_provider = st.session_state.chat_model_provider
    new_provider = st.selectbox(
        "Provider",
        options=_PROVIDERS,
        index=_PROVIDERS.index(cur_provider) if cur_provider in _PROVIDERS else 0,
        key="dlg_chat_model_provider",
    )
    new_api_key = st.text_input(
        "API Key",
        value=st.session_state.chat_model_api_key,
        key="dlg_chat_model_api_key",
        type="password",
        placeholder="sk-...",
    )
    new_base_url = st.text_input(
        "Base URL (optional)",
        value=st.session_state.chat_model_base_url,
        key="dlg_chat_model_base_url",
        placeholder="https://api.openai.com/v1",
    )
    new_vision = st.checkbox(
        "Supports Vision",
        value=st.session_state.chat_model_supports_vision,
        key="dlg_vision",
    )

    # Session indicators
    if st.session_state.messages:
        sessions = extract_sessions_from_messages(st.session_state.messages)
        if sessions:
            st.divider()
            st.subheader("Sandbox Sessions")
            for sid, info in sessions.items():
                status_map = {
                    "running": ("\U0001f7e2", "Active"),
                    "starting": ("\U0001f7e1", "Starting"),
                    "stopped": ("\U0001f534", "Stopped"),
                    "dead": ("\u26ab", "Dead"),
                }
                icon, label = status_map.get(info.status, ("\u26aa", info.status))
                runtime = f" ({info.runtime})" if info.runtime else ""
                st.markdown(f"{icon} `{sid}`{runtime} - {label}")

    st.divider()
    if st.button("Save", use_container_width=True, type="primary"):
        st.session_state.chat_model = new_model
        st.session_state.chat_model_provider = new_provider
        st.session_state.chat_model_api_key = new_api_key
        st.session_state.chat_model_base_url = new_base_url
        st.session_state.chat_model_supports_vision = new_vision
        st.rerun()


# ── Sidebar ─────────────────────────────────────────────

with st.sidebar:
    # Top: always visible
    if st.button(
        "\u2795  New conversation",
        use_container_width=True,
        type="primary",
    ):
        st.session_state.thread_id = None
        st.session_state.messages = []
        st.session_state.uploaded_file_metas = []
        st.rerun()

    # Middle: scrollable thread list (only threads with at least one message + response)
    _refresh_threads()
    threads = st.session_state.threads_list
    threads_with_content = [t for t in threads if _get_thread_preview(t.get("thread_id", ""))]

    with st.container(height=300, border=False):
        if threads_with_content:
            for t in threads_with_content:
                tid = t.get("thread_id", "")
                is_current = tid == st.session_state.thread_id
                updated = t.get("updated_at", "")

                preview = _get_thread_preview(tid)
                elapsed = time_ago(updated) if updated else ""

                col_main, col_del = st.columns([6, 1])
                with col_main:
                    display_text = preview or "New conversation"
                    if elapsed:
                        display_text += f"  \u00b7  {elapsed}"

                    if st.button(
                        display_text,
                        key=f"thread_{tid}",
                        use_container_width=True,
                        disabled=is_current,
                        type="tertiary",
                    ):
                        _switch_thread(tid)
                        st.rerun()
                with col_del:
                    if st.button(
                        "\U0001f5d1\ufe0f",
                        key=f"del_{tid}",
                        help="Delete conversation",
                    ):
                        try:
                            client.delete_thread(tid)
                            st.session_state.thread_previews.pop(tid, None)
                            if st.session_state.thread_id == tid:
                                st.session_state.thread_id = None
                                st.session_state.messages = []
                            _refresh_threads()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")
        else:
            st.caption("No conversations yet.")

    # Bottom: always visible
    if st.button("\u2699\ufe0f  Settings", use_container_width=True):
        _settings_dialog()


# ── Main Area ───────────────────────────────────────────

st.title("\U0001f4bb Sandbox Agent")

if not st.session_state.api_healthy:
    st.warning(
        "The Aegra API is not accessible at `http://127.0.0.1:8000`. "
        "Start the API with `uv run sandbox-agent api` or use `uv run sandbox-agent ui` (starts the API automatically)."
    )

# No auto-create: thread is created only when user sends first message and gets a response


# ── Render Message History ──────────────────────────────


def _render_human_message(msg: dict) -> None:
    """Render a HumanMessage."""
    with st.chat_message("user"):
        content = msg.get("content", "")
        if isinstance(content, str):
            st.markdown(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    st.markdown(block["text"])


def _render_ai_content(content: str | list) -> None:
    """Render AIMessage content (text + images)."""
    if isinstance(content, str) and content:
        st.markdown(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    st.markdown(block["text"])
                elif block.get("type") == "image_url":
                    _render_b64_image(block.get("image_url", {}).get("url", ""))


def _render_messages(messages: list[dict]) -> None:
    """Render message history with unified tool blocks (input+output together)."""
    sessions_map = {
        sid: s.runtime
        for sid, s in extract_sessions_from_messages(messages).items()
    }
    i = 0
    while i < len(messages):
        msg = messages[i]
        msg_type = msg.get("type", "")

        if msg_type in ("human", "HumanMessage"):
            _render_human_message(msg)
            i += 1
            continue

        # Assistant turn: collect AI + Tool messages until next Human
        assistant_msgs: list[dict] = []
        while i < len(messages) and messages[i].get("type") not in ("human", "HumanMessage"):
            assistant_msgs.append(messages[i])
            i += 1

        if not assistant_msgs:
            continue

        items, final_content = collect_tool_blocks(assistant_msgs)

        last_block_idx = next(
            (i for i in range(len(items) - 1, -1, -1) if items[i].get("type") == "block"),
            -1,
        )
        for idx, item in enumerate(items):
            if item.get("type") == "thought":
                _render_thought_block(item.get("text", ""))
            elif item.get("type") == "block":
                blk = item["block"]
                bid = blk.get("tool_call_id", str(idx))
                _render_tool_block(
                    blk,
                    sessions=sessions_map,
                    expanded=(idx == last_block_idx),
                    block_id=bid,
                )

        if final_content or assistant_msgs:
            last_ai = None
            for m in reversed(assistant_msgs):
                if m.get("type") in ("ai", "AIMessage", "AIMessageChunk"):
                    last_ai = m
                    break
            if last_ai:
                content = last_ai.get("content", "")
                if content:
                    with st.chat_message("assistant"):
                        _render_ai_content(content)


def _render_thought_block(text: str) -> None:
    """Render agent thinking/reasoning block (expandable)."""
    if not text or not text.strip():
        return
    with st.expander("\U0001f4ad Agent thinking", expanded=True):
        st.markdown(text)


def _render_b64_image(url: str) -> None:
    """Render a base64 data URL as an image."""
    if not url:
        return
    if url.startswith("data:image"):
        b64_part = url.split(",", 1)[-1] if "," in url else ""
        if b64_part:
            st.image(decode_b64_image(b64_part))
    else:
        st.image(url)


def _render_tool_block(
    block: dict,
    sessions: dict[str, str] | None = None,
    key_suffix: str = "",
    expanded: bool = False,
    block_id: str = "",
) -> None:
    """Render a unified tool block (input + output) with CLI-style formatting.

    sessions: optional dict mapping session_id -> runtime for execute_code
    syntax highlighting (python, javascript, r, julia).
    key_suffix: optional suffix appended to widget keys to avoid duplicates during streaming.
    block_id: unique id for widget keys (e.g. tool_call_id or index).
    """
    name = block.get("name", "tool")
    args = block.get("args", {})
    output = block.get("output")

    input_text, input_lang = format_tool_input_display(
        {"name": name, "args": args}, sessions=sessions
    )

    # Status label for expander title
    if output is None:
        status_label = "Running..."
    else:
        _, is_error = format_tool_output_display(output, name)
        status_label = "ERROR" if is_error else "OK"

    def _render_content() -> None:
        """Inner content: input + output in same block."""
        st.markdown('<div class="tool-block-input"><strong>Input</strong></div>', unsafe_allow_html=True)
        st.code(input_text, language=input_lang)
        st.markdown('<div class="tool-block-output"><strong>Output</strong></div>', unsafe_allow_html=True)

    # Build a fake message dict for parse_tool_message when we have output
    if output is not None and name in ("import_files", "export_files", "create_session", "stop_session"):
        fake_msg = {"name": name, "content": output}
        parsed = parse_tool_message(fake_msg)

    def _content_import_export() -> None:
        _render_content()
        _render_file_results(
            parsed,
            thread_id=st.session_state.thread_id,
            client=client,
            key_suffix=key_suffix,
        )

    def _content_create_session() -> None:
        _render_content()
        info = parsed.session_info
        sid = info.get("session_id", "")
        runtime = info.get("runtime", "")
        status = info.get("status", "")
        icon = "\U0001f7e2" if status == "running" else "\U0001f7e1"
        st.info(f"{icon} Session `{sid}` ({runtime}) - {status}")

    def _content_stop_session() -> None:
        _render_content()
        sid = parsed.session_info.get("session_id", "")
        st.warning(f"\U0001f534 Session `{sid}` stopped")

    def _content_figures() -> None:
        _render_content()
        if parsed.text_summary:
            st.markdown(parsed.text_summary)
        for fig_b64 in parsed.figures_b64:
            st.image(decode_b64_image(fig_b64))

    def _content_generic() -> None:
        _render_content()
        if output is None:
            st.caption("\u23f3 Running...")
        else:
            formatted, _ = format_tool_output_display(output, name)
            st.code(formatted, language="json")
            parsed_out = parse_tool_message({"name": name, "content": output})
            for fig_b64 in parsed_out.figures_b64:
                st.image(decode_b64_image(fig_b64))

    # Build a fake message dict for parse_tool_message when we have output
    if output is not None and name in ("import_files", "export_files", "create_session", "stop_session"):
        fake_msg = {"name": name, "content": output}
        parsed = parse_tool_message(fake_msg)

        if name in ("import_files", "export_files") and parsed.file_results:
            with st.expander(f"\U0001f527 {name} — {status_label}", expanded=expanded):
                _content_import_export()
            return

        if name == "create_session" and parsed.session_info:
            with st.expander(f"\U0001f527 {name} — {status_label}", expanded=expanded):
                _content_create_session()
            return

        if name == "stop_session" and parsed.session_info:
            with st.expander(f"\U0001f527 {name} — {status_label}", expanded=expanded):
                _content_stop_session()
            return

        if parsed.figures_b64:
            with st.expander(f"\U0001f527 {name} — {status_label}", expanded=expanded):
                _content_figures()
            return

    # Generic output: formatted JSON/text + figures (e.g. execute_code matplotlib/ggplot)
    with st.expander(f"\U0001f527 {name} — {status_label}", expanded=expanded):
        _content_generic()


def _render_file_results(
    parsed: ParsedToolResult,
    thread_id: str | None = None,
    client: AegraClient | None = None,
    key_suffix: str = "",
) -> None:
    """Render import/export file operation results."""
    is_export = parsed.tool_name == "export_files"
    label = "Export" if is_export else "Import"

    for fr in parsed.file_results:
        success = fr.get("success", False)
        source = fr.get("source", "")
        dest = fr.get("destination", "")
        session_id = fr.get("session_id", "")
        path = fr.get("path", "")
        size = fr.get("size", 0)
        error = fr.get("error", "")
        filename = (
            Path(path).name if path
            else Path(source).name if source
            else Path(dest).name
        )

        icon = get_file_icon(filename)
        size_str = format_file_size(size) if size else ""

        if success:
            if is_export and session_id and path and thread_id and client:
                try:
                    file_bytes = client.download_exported_file(thread_id, session_id, path)
                    available = bool(file_bytes)
                except Exception:
                    file_bytes = b""
                    available = False
                btn_col, status_col = st.columns([1, 1])
                with btn_col:
                    st.download_button(
                        label="Download",
                        data=file_bytes or b"",
                        file_name=filename,
                        key=f"dl_{session_id}_{path}{key_suffix}",
                        type="primary",
                        disabled=not available,
                    )
                with status_col:
                    st.caption(f"{icon} {filename} ({size_str}) — {'Available' if available else 'Unavailable'}")
            elif is_export and session_id and path:
                btn_col, status_col = st.columns([1, 1])
                with btn_col:
                    st.download_button(
                        label="Download",
                        data=b"",
                        file_name=filename,
                        key=f"dl_{session_id}_{path}{key_suffix}",
                        disabled=True,
                    )
                with status_col:
                    st.caption(f"{icon} {filename} ({size_str}) — Unavailable")
            else:
                st.caption(f"{icon} {filename} ({size_str}) — {label} OK")
        else:
            st.error(f"{icon} {filename} - Failed: {error}")


# Render existing messages
_render_messages(st.session_state.messages)


# ── Chat Input ──────────────────────────────────────────

prompt = st.chat_input(
    "Send a message...",
    accept_file="multiple",
    file_type=None,
    disabled=st.session_state.running or not st.session_state.api_healthy,
)

if prompt is not None:
    text = prompt.text if hasattr(prompt, "text") else (prompt if isinstance(prompt, str) else "")
    files = prompt.files if hasattr(prompt, "files") else []

    if not text and not files:
        st.stop()

    # Create thread on first message (no empty threads in history)
    if st.session_state.thread_id is None and st.session_state.api_healthy:
        tid = _create_new_thread()
        if not tid:
            st.stop()

    # Save uploaded files to STORAGE_DIR/<thread_id>/uploads/ (cleaned when thread is evicted)
    file_metas: list[dict] = []
    if files and st.session_state.thread_id:
        file_metas = save_uploaded_files(st.session_state.thread_id, files)
        st.session_state.uploaded_file_metas.extend(file_metas)

    # Build full content
    full_content = build_user_content(text, file_metas)

    if not full_content.strip():
        st.stop()

    # Add user message to state and render
    user_msg = {"type": "human", "content": full_content}
    st.session_state.messages.append(user_msg)

    with st.chat_message("user"):
        if file_metas:
            cols = st.columns(min(len(file_metas), 4))
            for i, fm in enumerate(file_metas):
                with cols[i % len(cols)]:
                    st.markdown(
                        f"{get_file_icon(fm['name'])} **{fm['name']}**  \n"
                        f"_{format_file_size(fm['size'])}_"
                    )
        if text:
            st.markdown(text)

    # Stream the response
    if st.session_state.thread_id and st.session_state.api_healthy:
        st.session_state.running = True

        input_messages = [{"role": "human", "content": full_content}]
        configurable = _get_configurable()

        try:
            assistant_placeholder = st.empty()

            final_messages: list[dict] = []
            _render_iter = 0

            for event in client.stream_run(
                thread_id=st.session_state.thread_id,
                input_messages=input_messages,
                configurable=configurable,
            ):
                if event.event == "values" and isinstance(event.data, dict):
                    new_messages = event.data.get("messages", [])
                    if new_messages:
                        final_messages = new_messages
                        st.session_state.messages = final_messages
                        _render_iter += 1

                        # Assistant turn: messages after last HumanMessage
                        last_human_idx = -1
                        for i in range(len(new_messages) - 1, -1, -1):
                            if new_messages[i].get("type") in ("human", "HumanMessage"):
                                last_human_idx = i
                                break
                        assistant_msgs = new_messages[last_human_idx + 1 :] if last_human_idx >= 0 else new_messages

                        items, final_content = collect_tool_blocks(assistant_msgs)
                        sessions_map = {
                            sid: s.runtime
                            for sid, s in extract_sessions_from_messages(new_messages).items()
                        }
                        last_block_idx = next(
                            (i for i in range(len(items) - 1, -1, -1) if items[i].get("type") == "block"),
                            -1,
                        )

                        try:
                            with assistant_placeholder.container():
                                for idx, item in enumerate(items):
                                    if item.get("type") == "thought":
                                        _render_thought_block(item.get("text", ""))
                                    elif item.get("type") == "block":
                                        blk = item["block"]
                                        bid = blk.get("tool_call_id", str(idx))
                                        _render_tool_block(
                                            blk,
                                            sessions=sessions_map,
                                            key_suffix=f"_s{_render_iter}",
                                            expanded=(idx == last_block_idx),
                                            block_id=bid,
                                        )
                                if final_content:
                                    with st.chat_message("assistant"):
                                        st.markdown(final_content)
                                elif not items:
                                    with st.chat_message("assistant"):
                                        st.markdown("\u23f3 Processing...")
                        except Exception:
                            pass

            if final_messages:
                st.session_state.messages = final_messages
                tid = st.session_state.thread_id
                if tid and not st.session_state.thread_previews.get(tid):
                    first_line = (text or full_content).strip().split("\n")[0]
                    st.session_state.thread_previews[tid] = (
                        first_line[:40] + ("..." if len(first_line) > 40 else "")
                    )

        except Exception as e:
            st.error(f"Error during execution: {e}")

        finally:
            st.session_state.running = False

        st.rerun()
