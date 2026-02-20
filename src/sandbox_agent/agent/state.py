"""Agent state definition for LangGraph."""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    """State for the Sandbox Agent.

    Attributes:
        messages: Conversation history (managed by LangGraph's ``add_messages`` reducer).
    """

    messages: Annotated[list[BaseMessage], add_messages]
