"""Unit tests for OdontokingAgent._tool_call interrupt handling.

ask_human pauses the graph by raising GraphInterrupt (a GraphBubbleUp subclass, which is
also an Exception). The tool-execution node must let that signal bubble up to pause the
graph, while still converting ordinary tool errors into an error ToolMessage.
"""

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphInterrupt

from langgraph.graph import END, StateGraph
from langgraph.types import Command

from app.core.langgraph.odontoking_graph import OdontokingAgent, _extract_handoff, _extract_mensaje
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

    # httpx is shared: patch after building the agent, whose OpenAI client needs the real one.
    agent = OdontokingAgent()
    monkeypatch.setattr(whatsapp_flows.httpx, "AsyncClient", _Client)
    agent.tools_by_name = {"responder_recordatorio": whatsapp_flows.responder_recordatorio}

    state = _state_with_tool_call("responder_recordatorio")
    state.messages[-1].tool_calls[0]["args"] = {"respuesta": "no_asiste", "activity_id": 7}
    command = await agent._tool_call(state, {"metadata": {"conversation_id": 4029}})

    assert posted[0][0].endswith("/conversations/4029/appointments/respond")
    assert "missing_conversation_id" not in command.update["messages"][0].content


@pytest.mark.asyncio
async def test_already_answered_reminder_is_not_a_handoff(monkeypatch):
    """A 409 means the reminder was answered: no failure, so the bot stays in the conversation."""
    from app.core.langgraph.tools import whatsapp_flows

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            class _Resp:
                status_code = 409
                text = "{}"

            return _Resp()

    agent = OdontokingAgent()
    monkeypatch.setattr(whatsapp_flows.httpx, "AsyncClient", _Client)
    agent.tools_by_name = {"responder_recordatorio": whatsapp_flows.responder_recordatorio}

    state = _state_with_tool_call("responder_recordatorio")
    state.messages[-1].tool_calls[0]["args"] = {"respuesta": "confirma", "activity_id": 7}
    command = await agent._tool_call(state, {"metadata": {"conversation_id": 3516}})

    assert command.goto == "chat"
    assert "already_answered" in command.update["messages"][0].content


def _failing_tool(result: str) -> AsyncMock:
    tool = AsyncMock()
    tool.ainvoke = AsyncMock(return_value=result)
    return tool


@pytest.mark.asyncio
@pytest.mark.parametrize("fuera_de_horario", [False, True])
async def test_failed_tool_hands_off_with_fixed_text(fuera_de_horario):
    """The model does not get to word the reply: reception takes over, no problem is mentioned."""
    agent = OdontokingAgent()
    agent.tools_by_name = {
        "responder_recordatorio": _failing_tool('{"success": false, "error": "http_error", "handoff": true}')
    }

    command = await agent._tool_call(
        _state_with_tool_call("responder_recordatorio"),
        {"metadata": {"conversation_id": 1, "fuera_de_horario": fuera_de_horario}},
    )

    assert command.goto == END
    reply = command.update["messages"][-1]
    assert _extract_handoff(reply.content) == {
        "action": "handoff",
        "motivo": "Una herramienta del agente falló",
        "fuera_de_horario": fuera_de_horario,
    }
    texto = _extract_mensaje(reply.content).lower()
    assert "recepcionista" in texto
    for palabra in ("problema", "error", "falla", "demanda", "disculp"):
        assert palabra not in texto


@pytest.mark.asyncio
async def test_a_tool_that_worked_goes_back_to_the_model():
    agent = OdontokingAgent()
    agent.tools_by_name = {"get_services": _failing_tool('{"success": true}')}

    command = await agent._tool_call(_state_with_tool_call("get_services"), {"metadata": {}})

    assert command.goto == "chat"


@pytest.mark.asyncio
async def test_retry_is_left_to_the_model():
    """A temporary failure does not silence the bot: the patient can try again."""
    agent = OdontokingAgent()
    agent.tools_by_name = {"verify_insurance": _failing_tool('{"retry": true}')}

    command = await agent._tool_call(_state_with_tool_call("verify_insurance"), {"metadata": {}})

    assert command.goto == "chat"


def test_forms_prompt_has_no_excuse_text():
    with open("app/core/prompts/odontoking.md") as f:
        template = f.read()
    assert "alta demanda" not in template.split("sin mencionar errores")[1]
    assert "mil disculpas" not in template


@pytest.mark.asyncio
async def test_failed_tool_ends_the_turn_in_a_compiled_graph():
    """Through a real graph: the fixed reply is the last message and the model is not called again."""
    agent = OdontokingAgent()
    agent.tools_by_name = {
        "responder_recordatorio": _failing_tool('{"success": false, "error": "http_error", "handoff": true}')
    }
    llm_calls = []

    async def chat(state, config):
        llm_calls.append(1)
        ai = AIMessage(content="", tool_calls=[{"name": "responder_recordatorio", "args": {}, "id": "c1"}])
        return Command(update={"messages": [ai]}, goto="tool_call")

    builder = StateGraph(GraphState)
    builder.add_node("chat", chat, destinations=("tool_call", END))
    builder.add_node("tool_call", agent._tool_call, destinations=("chat", END))
    builder.set_entry_point("chat")
    graph = builder.compile()

    result = await graph.ainvoke({"messages": []}, {"metadata": {"conversation_id": 1}})

    assert len(llm_calls) == 1
    assert "recepcionista" in result["messages"][-1].content
    assert _extract_handoff(result["messages"][-1].content) is not None
