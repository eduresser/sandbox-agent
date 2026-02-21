"""LangGraph agent definition using the ReAct pattern."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from sandbox_agent.agent.prompts import SYSTEM_PROMPT
from sandbox_agent.agent.state import AgentState
from sandbox_agent.clients import get_chat_model
from sandbox_agent.sandbox.manager import SandboxManager
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


def _process_execute_code_content(raw: str, vision: bool) -> str | list[dict]:
    """Post-process the JSON string returned by execute_code.

    If *vision* is True and figures are present, returns a multimodal content
    list (text + image_url blocks).  Otherwise strips figures and returns a
    plain JSON string.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw

    if not isinstance(parsed, dict):
        return raw

    figures = parsed.pop("figures", []) or []

    if vision and figures:
        text_json = json.dumps(parsed, ensure_ascii=False)
        content: list[dict] = [{"type": "text", "text": text_json}]
        for fig_b64 in figures:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{fig_b64}"},
            })
        return content

    return json.dumps(parsed, ensure_ascii=False)


def build_agent(
    *,
    manager: SandboxManager | None = None,
    llm: BaseChatModel | None = None,
) -> Any:
    """Build and compile the LangGraph agent.

    Args:
        manager: Shared SandboxManager instance. If ``None``, creates one.
        llm: LLM instance. If ``None``, uses the cached model from clients.

    Returns:
        Compiled LangGraph ``CompiledGraph`` ready to ``.invoke()`` or ``.stream()``.
    """
    if manager is None:
        manager = SandboxManager()

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

    def _vision_enabled() -> bool:
        v = vision_state["supported"]
        return v is True or v is None  # None means "try it"

    def tool_node(state: AgentState) -> dict[str, Any]:
        last_msg = state["messages"][-1]
        assert isinstance(last_msg, AIMessage) and last_msg.tool_calls

        result_messages: list[ToolMessage] = []
        for tc in last_msg.tool_calls:
            tool_fn = tools_by_name[tc["name"]]
            raw_result = tool_fn.invoke(tc["args"])

            if tc["name"] == "execute_code":
                content = _process_execute_code_content(raw_result, _vision_enabled())
            else:
                content = raw_result

            result_messages.append(
                ToolMessage(content=content, tool_call_id=tc["id"], name=tc["name"])
            )

        return {"messages": result_messages}

    def call_model(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]

        has_images = any(
            isinstance(m, ToolMessage) and isinstance(m.content, list)
            for m in messages
        )

        try:
            response = llm_with_tools.invoke(messages)
        except Exception as exc:
            if has_images and vision_state["supported"] is None:
                logger.info(
                    "Vision probe failed (%s: %s), retrying without images.",
                    type(exc).__name__, exc,
                )
                vision_state["supported"] = False
                messages = _strip_images_from_messages(messages)
                response = llm_with_tools.invoke(messages)
            else:
                raise

        if has_images and vision_state["supported"] is None:
            vision_state["supported"] = True
            logger.info("Vision probe succeeded, enabling multimodal figures.")

        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")

    return graph.compile()
