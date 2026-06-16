"""Unit tests for OdontokingAgent._tool_call interrupt handling.

ask_human pauses the graph by raising GraphInterrupt (a GraphBubbleUp subclass, which is
also an Exception). The tool-execution node must let that signal bubble up to pause the
graph, while still converting ordinary tool errors into an error ToolMessage.
"""

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphInterrupt

from app.core.langgraph.odontoking_graph import OdontokingAgent
from app.schemas import GraphState


def _state_with_tool_call(name: str) -> GraphState:
    ai = AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": "call_1"}])
    return GraphState(messages=[ai])


@pytest.mark.asyncio
async def test_tool_call_reraises_graph_interrupt():
    """A tool raising GraphInterrupt must propagate, not be swallowed into a ToolMessage."""
    agent = OdontokingAgent()
    fake_tool = AsyncMock()
    fake_tool.ainvoke = AsyncMock(side_effect=GraphInterrupt(()))
    agent.tools_by_name = {"ask_human": fake_tool}

    with pytest.raises(GraphInterrupt):
        await agent._tool_call(_state_with_tool_call("ask_human"))


@pytest.mark.asyncio
async def test_tool_call_swallows_ordinary_errors():
    """A normal tool error becomes an error ToolMessage so the graph can continue."""
    agent = OdontokingAgent()
    fake_tool = AsyncMock()
    fake_tool.ainvoke = AsyncMock(side_effect=ValueError("boom"))
    agent.tools_by_name = {"get_services": fake_tool}

    command = await agent._tool_call(_state_with_tool_call("get_services"))
    msg = command.update["messages"][0]
    assert isinstance(msg, ToolMessage)
    assert "boom" in msg.content
