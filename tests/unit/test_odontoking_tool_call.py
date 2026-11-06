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


@pytest.mark.asyncio
async def test_tool_call_injects_conversation_id_for_enviar_formulario():
    """enviar_formulario gets conversation_id from the graph config metadata, not from the LLM."""
    agent = OdontokingAgent()
    fake_tool = AsyncMock()
    fake_tool.ainvoke = AsyncMock(return_value='{"success": true, "flow": "paciente_tercero"}')
    agent.tools_by_name = {"enviar_formulario": fake_tool}

    state = _state_with_tool_call("enviar_formulario")
    state.messages[-1].tool_calls[0]["args"] = {"flow": "paciente_tercero"}
    await agent._tool_call(state, {"metadata": {"conversation_id": 123}})

    # conversation_id is added by the graph, never by the LLM. The greeting is NOT a tool arg
    # anymore (it rides in the reply text via _prepend_saludo), so `saludar` must not appear.
    fake_tool.ainvoke.assert_awaited_once()
    sent = fake_tool.ainvoke.await_args.args[0]
    assert sent["flow"] == "paciente_tercero"
    assert sent["conversation_id"] == 123
    assert "saludar" not in sent


def test_prepend_saludo_first_contact_prepends_welcome():
    """On the first message of a conversation the reply is prefixed with the welcome, any intent."""
    from app.core.langgraph.odontoking_graph import _prepend_saludo

    out = _prepend_saludo("Le envié un formulario para agendar su cita 🙌", es_primer_contacto=True)
    assert out.startswith("¡Hola! 👋 Bienvenido(a) a Odontoking.")
    assert "Le envié un formulario" in out


def test_prepend_saludo_not_first_contact_is_noop():
    """When it is not the first contact, the reply is returned unchanged (no re-greeting)."""
    from app.core.langgraph.odontoking_graph import _prepend_saludo

    msg = "Le envié un formulario 🙌"
    assert _prepend_saludo(msg, es_primer_contacto=False) == msg


def test_prepend_saludo_skips_when_already_greeted():
    """If the reply already opens with a welcome (e.g. the menu), it is not greeted twice."""
    from app.core.langgraph.odontoking_graph import _prepend_saludo

    menu = "¡Hola! 👋 Bienvenido(a) a Odontoking.\n\n1. Agendar..."
    assert _prepend_saludo(menu, es_primer_contacto=True) == menu


@pytest.mark.asyncio
async def test_tool_call_other_tools_not_given_conversation_id():
    """Non-form tools are invoked with their args untouched (no conversation_id leak)."""
    agent = OdontokingAgent()
    fake_tool = AsyncMock()
    fake_tool.ainvoke = AsyncMock(return_value="{}")
    agent.tools_by_name = {"get_citas": fake_tool}

    state = _state_with_tool_call("get_citas")
    state.messages[-1].tool_calls[0]["args"] = {"wa_id": "591700"}
    await agent._tool_call(state, {"metadata": {"conversation_id": 123}})

    fake_tool.ainvoke.assert_awaited_once_with({"wa_id": "591700"})


@pytest.mark.asyncio
async def test_tool_call_injects_conversation_id_for_responder_recordatorio():
    """A reminder answered in a sentence goes through the LLM and must reach the CRM."""
    agent = OdontokingAgent()
    fake_tool = AsyncMock()
    fake_tool.ainvoke = AsyncMock(return_value='{"success": true}')
    agent.tools_by_name = {"responder_recordatorio": fake_tool}

    state = _state_with_tool_call("responder_recordatorio")
    state.messages[-1].tool_calls[0]["args"] = {"respuesta": "no_asiste", "activity_id": 7}
    await agent._tool_call(state, {"metadata": {"conversation_id": 123}})

    fake_tool.ainvoke.assert_awaited_once_with({"respuesta": "no_asiste", "activity_id": 7, "conversation_id": 123})


@pytest.mark.asyncio
async def test_real_responder_recordatorio_reaches_the_crm_with_injected_id(monkeypatch):
    """End to end through the real tool: the injected id builds the CRM URL."""
    from app.core.langgraph.tools import whatsapp_flows

    posted = []

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            posted.append((url, json))

            class _Resp:
                status_code = 200
                text = "{}"

                def json(self):
                    return {"success": True}

            return _Resp()

    monkeypatch.setattr(whatsapp_flows.httpx, "AsyncClient", _Client)
    agent = OdontokingAgent()
    agent.tools_by_name = {"responder_recordatorio": whatsapp_flows.responder_recordatorio}

    state = _state_with_tool_call("responder_recordatorio")
    state.messages[-1].tool_calls[0]["args"] = {"respuesta": "no_asiste", "activity_id": 7}
    command = await agent._tool_call(state, {"metadata": {"conversation_id": 4029}})

    assert posted[0][0].endswith("/conversations/4029/appointments/respond")
    assert "missing_conversation_id" not in command.update["messages"][0].content
