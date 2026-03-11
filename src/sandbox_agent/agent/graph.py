"""LangGraph agent definition using the ReAct pattern."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from sandbox_agent.agent.configuration import Configuration
from sandbox_agent.agent.prompts import SYSTEM_PROMPT
from sandbox_agent.agent.state import AgentState
from sandbox_agent.clients import get_chat_model
from sandbox_agent.sandbox import SandboxManager, current_thread_id, get_manager
from sandbox_agent.settings import get_settings
from sandbox_agent.tools import create_tools

logger = logging.getLogger(__name__)


def _strip_images_from_messages(messages: list) -> list:
    """Return a copy of *messages* with all image_url blocks removed from ToolMessages."""
    cleaned: list = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and isinstance(msg.content, list):
            text_blocks = [b for b in msg.content if b.get("type") != "image_url"]
            if len(text_blocks) == 1 and text_blocks[0].get("type") == "text":
                new_content = text_blocks[0]["text"]
            else:
                new_content = text_blocks or ""
            cleaned.append(
                ToolMessage(
                    content=new_content,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                )
            )
        else:
            cleaned.append(msg)
    return cleaned


def _prepare_messages_for_llm(messages: list, vision: bool) -> list:
    """Return a lightweight copy of *messages* suitable for the LLM.

    Heavy ``display_outputs`` payloads (base64 images, HTML, audio …) in
    ToolMessages are replaced with brief summaries so they don't bloat the
    context window.  When *vision* is enabled, image outputs are re-attached
    as efficient ``image_url`` content blocks so the model can still "see"
    them without polluting the text context.

    The full data remains in the persisted state so the frontend can render
    rich outputs as usual.
    """
    prepared: list = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            prepared.append(msg)
            continue

        if isinstance(msg.content, list):
            text_parts = [b for b in msg.content if b.get("type") == "text"]
            raw_text = text_parts[0]["text"] if text_parts else ""
        elif isinstance(msg.content, str):
            raw_text = msg.content
        else:
            prepared.append(msg)
            continue

        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            prepared.append(msg)
            continue

        if not isinstance(parsed, dict) or not parsed.get("display_outputs"):
            if isinstance(msg.content, list):
                prepared.append(
                    ToolMessage(content=raw_text, tool_call_id=msg.tool_call_id, name=msg.name)
                )
            else:
                prepared.append(msg)
            continue

        display_outputs = parsed.pop("display_outputs")
        summaries: list[str] = []
        image_blocks: list[dict] = []
        for out in display_outputs:
            mime = out.get("type", "unknown")
            if mime.startswith("image/") and vision and out.get("data"):
                image_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{out['data']}"},
                })
                summaries.append(f"[{mime} image displayed to user]")
            else:
                summaries.append(f"[{mime} output displayed to user]")

        if summaries:
            parsed["display_outputs_summary"] = summaries

        new_text = json.dumps(parsed, ensure_ascii=False)

        if image_blocks:
            new_content: str | list = [{"type": "text", "text": new_text}] + image_blocks
        else:
            new_content = new_text

        prepared.append(
            ToolMessage(content=new_content, tool_call_id=msg.tool_call_id, name=msg.name)
        )

    return prepared


def build_agent(
    *,
    manager: SandboxManager | None = None,
    llm: BaseChatModel | None = None,
    checkpointer: Any = None,
) -> Any:
    """Build and compile the LangGraph agent.

    Args:
        manager: Shared SandboxManager instance. If ``None``, creates one.
        llm: LLM instance. If ``None``, uses the cached model from clients.
        checkpointer: Optional checkpointer for persistence (e.g. PostgresSaver).
            When provided, use ``config={"configurable": {"thread_id": "..."}}`` on invoke/stream.

    Returns:
        Compiled LangGraph ``CompiledGraph`` ready to ``.invoke()`` or ``.stream()``.
    """
    if manager is None:
        manager = get_manager()

    if llm is None:
        llm = get_chat_model()

    settings = get_settings()
    tools = create_tools(manager)
    tools_by_name = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools)

    # None = not yet probed; True/False = confirmed
    vision_override = settings.CHAT_MODEL_SUPPORTS_VISION
    vision_state: dict[str, bool | None] = {
        "supported": vision_override,
    }

    def _vision_enabled(cfg: Configuration) -> bool:
        if cfg.chat_model_supports_vision is not None:
            return cfg.chat_model_supports_vision
        v = vision_state["supported"]
        return v is True or v is None  # None means "try it"

    def tool_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        thread_id = (config.get("configurable") or {}).get("thread_id")
        token = current_thread_id.set(thread_id)
        try:
            last_msg = state["messages"][-1]
            assert isinstance(last_msg, AIMessage) and last_msg.tool_calls

            result_messages: list[ToolMessage] = []
            for tc in last_msg.tool_calls:
                tool_fn = tools_by_name[tc["name"]]

                try:
                    raw_result = tool_fn.invoke(tc["args"])
                except Exception as exc:
                    payload = {
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "hint": "Fix the invalid parameters and try again.",
                    }
                    result_messages.append(
                        ToolMessage(
                            content=json.dumps(payload, ensure_ascii=False),
                            tool_call_id=tc["id"],
                            name=tc["name"],
                        )
                    )
                    continue

                content = raw_result

                result_messages.append(
                    ToolMessage(content=content, tool_call_id=tc["id"], name=tc["name"])
                )

            return {"messages": result_messages}
        finally:
            current_thread_id.reset(token)

    _override_cache: dict[str, BaseChatModel] = {}

    def _get_llm_for_config(cfg: Configuration) -> BaseChatModel:
        """Return an LLM bound with tools, using overrides from Configuration."""
        cache_key = (
            f"{cfg.chat_model}"
            f"|{cfg.chat_model_provider}"
            f"|{hash(cfg.chat_model_api_key or '')}"
            f"|{cfg.chat_model_base_url or ''}"
        )

        default_key = (
            f"{settings.CHAT_MODEL}"
            f"|{settings.CHAT_MODEL_PROVIDER}"
            f"|{hash(settings.CHAT_MODEL_API_KEY or '')}"
            f"|{settings.CHAT_MODEL_BASE_URL or ''}"
        )
        if cache_key == default_key:
            return llm_with_tools

        if cache_key in _override_cache:
            return _override_cache[cache_key]

        kwargs: dict[str, Any] = {
            "model": cfg.chat_model,
            "model_provider": cfg.chat_model_provider,
        }
        if cfg.chat_model_base_url:
            kwargs["base_url"] = cfg.chat_model_base_url
        if cfg.chat_model_api_key:
            kwargs["api_key"] = cfg.chat_model_api_key

        override = init_chat_model(**kwargs).bind_tools(tools)
        _override_cache[cache_key] = override
        return override

    def call_model(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]

        cfg = Configuration.from_runnable_config(config)
        active_llm = _get_llm_for_config(cfg)

        if cfg.chat_model_supports_vision is not None:
            vision_state["supported"] = cfg.chat_model_supports_vision

        vision = _vision_enabled(cfg)
        messages = _prepare_messages_for_llm(messages, vision)

        has_images = any(
            isinstance(m, ToolMessage) and isinstance(m.content, list)
            for m in messages
        )

        try:
            response = active_llm.invoke(messages)
        except Exception as exc:
            if has_images:
                logger.info(
                    "LLM call failed with images (%s), retrying without.",
                    type(exc).__name__,
                )
                vision_state["supported"] = False
                messages = _strip_images_from_messages(messages)
                response = active_llm.invoke(messages)
            else:
                raise

        if has_images and vision_state["supported"] is None:
            vision_state["supported"] = True
            logger.info("Vision probe succeeded, enabling multimodal display outputs.")

        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        msgs = state["messages"]
        if not msgs:
            return END
        last_message = msgs[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState, config_schema=Configuration)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")

    return graph.compile(checkpointer=checkpointer)


# ── Aegra / LangGraph Platform ─────────────────────────────────────────────

graph = build_agent(manager=get_manager(), checkpointer=None)
