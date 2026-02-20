"""LangGraph agent definition using the ReAct pattern."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from sandbox_agent.agent.prompts import SYSTEM_PROMPT
from sandbox_agent.agent.state import AgentState
from sandbox_agent.clients import get_chat_model
from sandbox_agent.sandbox.manager import SandboxManager
from sandbox_agent.tools import create_tools


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

    tools = create_tools(manager)
    llm_with_tools = llm.bind_tools(tools)

    def call_model(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(tools))

    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")

    return graph.compile()
