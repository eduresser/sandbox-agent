"""Streamlit frontend for the Sandbox Agent (Aegra API)."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
from api_client import AegraClient
from utils import (
    ParsedToolResult,
    build_user_content,
    check_exported_file,
    decode_b64_image,
    extract_sessions_from_messages,
    format_file_size,
    get_file_icon,
    parse_tool_message,
    read_exported_file,
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
        st.error(f"Erro ao criar thread: {e}")
        return None


def _switch_thread(thread_id: str) -> None:
    st.session_state.thread_id = thread_id
    st.session_state.messages = _load_thread_messages(thread_id)
    st.session_state.uploaded_file_metas = []


def _get_thread_preview(thread_id: str) -> str:
    """Return a short preview of the first human message in a thread."""
    previews = st.session_state.thread_previews
    if thread_id in previews:
        return previews[thread_id]

    try:
        state = client.get_thread_state(thread_id)
        msgs = state.get("values", {}).get("messages", [])
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

    previews[thread_id] = ""
    return ""


_PROVIDERS = ["openai", "anthropic", "google_genai", "azure_openai", "ollama", "fireworks"]


@st.dialog("Configuracoes")
def _settings_dialog() -> None:
    # Health check
    try:
        healthy = client.is_healthy()
    except Exception:
        healthy = False

    if healthy:
        st.success("API Conectada", icon="\u2705")
    else:
        st.error("API Indisponivel", icon="\u274c")
    st.session_state.api_healthy = healthy

    st.subheader("Modelo LLM")
    new_model = st.text_input(
        "Modelo",
        value=st.session_state.chat_model,
        key="dlg_chat_model",
        placeholder="gpt-4o, claude-sonnet-4-20250514, gemini-2.0-flash...",
    )
    cur_provider = st.session_state.chat_model_provider
    new_provider = st.selectbox(
        "Provedor",
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
        "Base URL (opcional)",
        value=st.session_state.chat_model_base_url,
        key="dlg_chat_model_base_url",
        placeholder="https://api.openai.com/v1",
    )
    new_vision = st.checkbox(
        "Suporta Vision",
        value=st.session_state.chat_model_supports_vision,
        key="dlg_vision",
    )

    # Session indicators
    if st.session_state.messages:
        sessions = extract_sessions_from_messages(st.session_state.messages)
        if sessions:
            st.divider()
            st.subheader("Sessoes Sandbox")
            for sid, info in sessions.items():
                status_map = {
                    "running": ("\U0001f7e2", "Ativa"),
                    "starting": ("\U0001f7e1", "Iniciando"),
                    "stopped": ("\U0001f534", "Encerrada"),
                    "dead": ("\u26ab", "Morta"),
                }
                icon, label = status_map.get(info.status, ("\u26aa", info.status))
                runtime = f" ({info.runtime})" if info.runtime else ""
                st.markdown(f"{icon} `{sid}`{runtime} - {label}")

    st.divider()
    if st.button("Salvar", use_container_width=True, type="primary"):
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
        "\u2795  Nova Conversa",
        use_container_width=True,
        type="primary",
    ):
        _create_new_thread()
        st.rerun()

    # Middle: scrollable thread list (CSS expands to fill space)
    _refresh_threads()
    threads = st.session_state.threads_list

    with st.container(height=300, border=False):
        if threads:
            for t in threads:
                tid = t.get("thread_id", "")
                is_current = tid == st.session_state.thread_id
                updated = t.get("updated_at", "")

                preview = _get_thread_preview(tid)
                elapsed = time_ago(updated) if updated else ""

                col_main, col_del = st.columns([6, 1])
                with col_main:
                    display_text = preview or "Nova conversa"
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
                        help="Deletar conversa",
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
                            st.error(f"Erro: {e}")
        else:
            st.caption("Nenhuma conversa ainda.")

    # Bottom: always visible
    if st.button("\u2699\ufe0f  Configuracoes", use_container_width=True):
        _settings_dialog()


# ── Main Area ───────────────────────────────────────────

st.title("\U0001f4bb Sandbox Agent")

if not st.session_state.api_healthy:
    st.warning(
        "A API Aegra nao esta acessivel em `http://127.0.0.1:8000`. "
        "Inicie o servidor com `uv run aegra dev` antes de usar o chat."
    )

# Auto-create thread if none exists
if st.session_state.thread_id is None and st.session_state.api_healthy:
    _create_new_thread()


# ── Render Message History ──────────────────────────────


def _render_message(msg: dict) -> None:
    """Render a single message in the chat UI."""
    msg_type = msg.get("type", "")

    if msg_type in ("human", "HumanMessage"):
        with st.chat_message("user"):
            content = msg.get("content", "")
            if isinstance(content, str):
                st.markdown(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        st.markdown(block["text"])

    elif msg_type in ("ai", "AIMessage", "AIMessageChunk"):
        with st.chat_message("assistant"):
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                st.markdown(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            st.markdown(block["text"])
                        elif block.get("type") == "image_url":
                            _render_b64_image(block.get("image_url", {}).get("url", ""))

            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    with st.expander(f"\U0001f527 {tc.get('name', 'tool')}", expanded=False):
                        args_json = json.dumps(
                            tc.get("args", {}), indent=2, ensure_ascii=False,
                        )
                        st.code(args_json, language="json")

    elif msg_type in ("tool", "ToolMessage"):
        _render_tool_message(msg)


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


def _render_tool_message(msg: dict) -> None:
    """Render a ToolMessage with appropriate formatting."""
    parsed = parse_tool_message(msg)
    tool_name = parsed.tool_name or "tool"

    with st.chat_message("assistant"):
        if parsed.tool_name in ("import_files", "export_files"):
            _render_file_results(parsed)
        elif parsed.tool_name == "create_session" and parsed.session_info:
            info = parsed.session_info
            sid = info.get("session_id", "")
            runtime = info.get("runtime", "")
            status = info.get("status", "")
            icon = "\U0001f7e2" if status == "running" else "\U0001f7e1"
            st.info(f"{icon} Sessao `{sid}` ({runtime}) - {status}")

        elif parsed.tool_name == "stop_session" and parsed.session_info:
            sid = parsed.session_info.get("session_id", "")
            st.warning(f"\U0001f534 Sessao `{sid}` encerrada")

        else:
            with st.expander(f"\U0001f4e4 {tool_name}", expanded=bool(parsed.figures_b64)):
                if parsed.text_summary:
                    st.markdown(parsed.text_summary)

        for fig_b64 in parsed.figures_b64:
            st.image(decode_b64_image(fig_b64), use_container_width=True)


def _render_file_results(parsed: ParsedToolResult) -> None:
    """Render import/export file operation results."""
    is_export = parsed.tool_name == "export_files"
    label = "Exportacao" if is_export else "Importacao"

    for fr in parsed.file_results:
        success = fr.get("success", False)
        source = fr.get("source", "")
        dest = fr.get("destination", "")
        size = fr.get("size", 0)
        error = fr.get("error", "")
        filename = Path(source).name if source else Path(dest).name

        icon = get_file_icon(filename)
        size_str = format_file_size(size) if size else ""

        if success:
            if is_export and dest:
                accessible = check_exported_file(dest)
                if accessible:
                    st.success(f"{icon} {filename} ({size_str}) - Disponivel")
                    file_bytes = read_exported_file(dest)
                    if file_bytes:
                        st.download_button(
                            label=f"Baixar {filename}",
                            data=file_bytes,
                            file_name=filename,
                            key=f"dl_{dest}",
                        )
                else:
                    st.warning(f"{icon} {filename} - Inacessivel (arquivo pode ter sido removido)")
            else:
                st.success(f"{icon} {filename} ({size_str}) - {label} OK")
        else:
            st.error(f"{icon} {filename} - Falha: {error}")


# Render existing messages
for msg in st.session_state.messages:
    _render_message(msg)


# ── Chat Input ──────────────────────────────────────────

prompt = st.chat_input(
    "Envie uma mensagem...",
    accept_file="multiple",
    file_type=None,
    disabled=st.session_state.running or not st.session_state.api_healthy,
)

if prompt is not None:
    text = prompt.text if hasattr(prompt, "text") else (prompt if isinstance(prompt, str) else "")
    files = prompt.files if hasattr(prompt, "files") else []

    if not text and not files:
        st.stop()

    # Save uploaded files
    file_metas: list[dict] = []
    if files and st.session_state.thread_id:
        file_metas = save_uploaded_files(st.session_state.thread_id, files)
        st.session_state.uploaded_file_metas.extend(file_metas)

    # Build full content
    full_content = build_user_content(text, file_metas)

    if not full_content.strip():
        st.stop()

    # Update thread preview cache with first line of first message
    tid = st.session_state.thread_id
    if tid and tid not in st.session_state.thread_previews:
        first_line = (text or full_content).strip().split("\n")[0]
        st.session_state.thread_previews[tid] = (
            first_line[:40] + ("..." if len(first_line) > 40 else "")
        )

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
            with st.chat_message("assistant"):
                response_placeholder = st.empty()
                response_placeholder.markdown("\u23f3 Processando...")

                final_messages: list[dict] = []
                last_ai_content = ""

                for event in client.stream_run(
                    thread_id=st.session_state.thread_id,
                    input_messages=input_messages,
                    configurable=configurable,
                ):
                    if event.event == "values" and isinstance(event.data, dict):
                        new_messages = event.data.get("messages", [])
                        if new_messages:
                            final_messages = new_messages

                            last_msg = new_messages[-1]
                            msg_type = last_msg.get("type", "")

                            if msg_type in ("ai", "AIMessage", "AIMessageChunk"):
                                content = last_msg.get("content", "")
                                if isinstance(content, str) and content:
                                    last_ai_content = content
                                    response_placeholder.markdown(content)
                                elif isinstance(content, list):
                                    text_parts = [
                                        b.get("text", "")
                                        for b in content
                                        if isinstance(b, dict) and b.get("type") == "text"
                                    ]
                                    if text_parts:
                                        last_ai_content = "\n".join(text_parts)
                                        response_placeholder.markdown(last_ai_content)

                                tool_calls = last_msg.get("tool_calls", [])
                                if tool_calls:
                                    names = ", ".join(tc.get("name", "") for tc in tool_calls)
                                    response_placeholder.markdown(
                                        f"\u23f3 Executando: {names}..."
                                    )

                response_placeholder.empty()

            # Replace messages with the final server state
            if final_messages:
                st.session_state.messages = final_messages

        except Exception as e:
            st.error(f"Erro durante execucao: {e}")

        finally:
            st.session_state.running = False

        st.rerun()
