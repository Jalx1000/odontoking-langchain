"""Unit tests for the WhatsApp native-forms tool (enviar_formulario).

Covers:
1. 200 → success JSON with the flow echoed back.
2. 422 (24h window closed) → success:false + handoff (derive to reception).
3. Missing conversation_id (not injected) → error + handoff, no HTTP call.
4. Invalid flow value → error, no HTTP call.
5. Transient failure (timeout) → service_unavailable + handoff.
6. Retry classifier: 422 is NOT retryable; timeouts / 5xx are.
7. The LLM-facing schema exposes flow + prefill params (conversation_id is injected server-side).
8. Prefill (relation/patient_name/relationship/specialty) is sent only for paciente_tercero,
   canonicalized with empties dropped, and omitted for cancelar/reprogramar.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.core.langgraph.tools.whatsapp_flows import (
    _is_retryable_flow_error,
    enviar_formulario,
    responder_recordatorio,
)


def _run(coro):
    """Run an async coroutine synchronously (pytest-asyncio is not installed)."""
    return asyncio.run(coro)


def _resp(status: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = ""
    if status >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            str(status), request=MagicMock(), response=r
        )
    else:
        r.raise_for_status = MagicMock()
    return r


def _async_client(resp: MagicMock) -> MagicMock:
    """Patch target for httpx.AsyncClient: `async with ... as client` yields a mock with .post."""
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def test_enviar_formulario_success():
    """200 → success JSON echoing the flow, posted to the conversations/flows endpoint."""
    factory = _async_client(_resp(200))
    with patch("app.core.langgraph.tools.whatsapp_flows.httpx.AsyncClient", factory):
        out = _run(enviar_formulario.ainvoke({"flow": "paciente_tercero", "conversation_id": 42}))
    data = json.loads(out)
    assert data == {"success": True, "flow": "paciente_tercero"}
    # Posted to the conversations/flows endpoint with the {"flow": ...} body.
    client = factory.return_value.__aenter__.return_value
    url = client.post.call_args.args[0]
    assert url.endswith("/api/v1/whatsapp/conversations/42/flows")
    assert client.post.call_args.kwargs["json"] == {"flow": "paciente_tercero"}


def test_enviar_formulario_window_closed_is_handoff():
    """422 (24h window closed) → success:false with handoff so the agent derives to reception."""
    factory = _async_client(_resp(422))
    with patch("app.core.langgraph.tools.whatsapp_flows.httpx.AsyncClient", factory):
        out = _run(enviar_formulario.ainvoke({"flow": "cancelar_cita", "conversation_id": 7}))
    data = json.loads(out)
    assert data["success"] is False
    assert data["error"] == "window_closed"
    assert data["handoff"] is True


def test_enviar_formulario_missing_conversation_id():
    """No conversation_id injected (e.g. meta gateway) → derive, and never hit the network."""
    with patch("app.core.langgraph.tools.whatsapp_flows.httpx.AsyncClient") as client_cls:
        out = _run(enviar_formulario.ainvoke({"flow": "reprogramar_cita"}))
    data = json.loads(out)
    assert data["success"] is False
    assert data["error"] == "missing_conversation_id"
    assert data["handoff"] is True
    client_cls.assert_not_called()


def test_enviar_formulario_invalid_flow():
    """An unknown flow value is rejected locally, without any network call."""
    with patch("app.core.langgraph.tools.whatsapp_flows.httpx.AsyncClient") as client_cls:
        out = _run(enviar_formulario.ainvoke({"flow": "borrar_todo", "conversation_id": 1}))
    data = json.loads(out)
    assert data["success"] is False
    assert data["error"] == "invalid_flow"
    client_cls.assert_not_called()


def test_enviar_formulario_transient_derives():
    """A transient error bubbling out of the retrying _post_flow → derive to reception."""
    async def _boom(*_a, **_k):
        raise httpx.ConnectError("boom")

    with patch("app.core.langgraph.tools.whatsapp_flows._post_flow", _boom):
        out = _run(enviar_formulario.ainvoke({"flow": "paciente_tercero", "conversation_id": 9}))
    data = json.loads(out)
    assert data["success"] is False
    assert data["error"] == "service_unavailable"
    assert data["handoff"] is True


def test_retry_classifier():
    """422 is never retried; 5xx and network errors are transient and retried."""
    # 422 is a definitive business outcome, never retried.
    resp422 = MagicMock(status_code=422)
    assert _is_retryable_flow_error(httpx.HTTPStatusError("422", request=MagicMock(), response=resp422)) is False
    # 5xx and network errors are transient → retried.
    resp500 = MagicMock(status_code=503)
    assert _is_retryable_flow_error(httpx.HTTPStatusError("503", request=MagicMock(), response=resp500)) is True
    assert _is_retryable_flow_error(httpx.ConnectError("x")) is True
    assert _is_retryable_flow_error(httpx.ReadTimeout("x")) is True


def test_llm_schema_exposes_flow_and_prefill_not_conversation_id():
    """conversation_id is injected server-side; the LLM-facing schema has flow + prefill params."""
    props = convert_to_openai_tool(enviar_formulario)["function"]["parameters"]["properties"]
    assert set(props.keys()) == {
        "flow", "relation", "patient_name", "relationship", "specialty", "doctor", "date", "time",
    }
    assert "conversation_id" not in props


def test_enviar_formulario_prefill_sent_for_booking():
    """paciente_tercero with greeting hints → prefill (canonicalized, empties dropped) in the body."""
    factory = _async_client(_resp(200))
    with patch("app.core.langgraph.tools.whatsapp_flows.httpx.AsyncClient", factory):
        out = _run(enviar_formulario.ainvoke({
            "flow": "paciente_tercero",
            "conversation_id": 42,
            "relation": "para_mi",       # alias → canonical "self"
            "patient_name": "  María Pérez ",
            "relationship": "Hijo",
            "specialty": "   ",          # blank → dropped
            "doctor": "Dr. Eloy",
            "date": "2026-10-30",
            "time": "13:00",
        }))
    assert json.loads(out)["success"] is True
    client = factory.return_value.__aenter__.return_value
    body = client.post.call_args.kwargs["json"]
    assert body["flow"] == "paciente_tercero"
    assert body["prefill"] == {
        "relation": "self",
        "patient_name": "María Pérez",
        "relationship": "hijo",
        "doctor": "Dr. Eloy",
        "date": "2026-10-30",
        "time": "13:00",
    }


def test_enviar_formulario_prefill_ignored_for_non_booking():
    """Prefill params are NOT sent for cancelar/reprogramar (the CRM ignores them anyway)."""
    factory = _async_client(_resp(200))
    with patch("app.core.langgraph.tools.whatsapp_flows.httpx.AsyncClient", factory):
        _run(enviar_formulario.ainvoke({
            "flow": "reprogramar_cita",
            "conversation_id": 7,
            "relation": "tercero",
            "specialty": "ortodoncia",
        }))
    client = factory.return_value.__aenter__.return_value
    body = client.post.call_args.kwargs["json"]
    assert body == {"flow": "reprogramar_cita"}
    assert "prefill" not in body


def test_enviar_formulario_doctor_id_sent_as_int():
    """A numeric doctor is sent as an int (CRM expects a numeric id, e.g. 12); a name stays text."""
    factory = _async_client(_resp(200))
    with patch("app.core.langgraph.tools.whatsapp_flows.httpx.AsyncClient", factory):
        _run(enviar_formulario.ainvoke({
            "flow": "paciente_tercero",
            "conversation_id": 3,
            "doctor": "12",            # numeric id → int
            "date": "2026-10-28",
            "time": "13:00",
        }))
    client = factory.return_value.__aenter__.return_value
    prefill = client.post.call_args.kwargs["json"]["prefill"]
    assert prefill == {"doctor": 12, "date": "2026-10-28", "time": "13:00"}
    assert isinstance(prefill["doctor"], int)


def test_responder_recordatorio_confirma():
    """'confirma' → POST a .../appointments/respond con respuesta + activity_id."""
    factory = _async_client(_resp(200))
    with patch("app.core.langgraph.tools.whatsapp_flows.httpx.AsyncClient", factory):
        out = _run(responder_recordatorio.ainvoke({
            "respuesta": "confirma", "activity_id": 55, "conversation_id": 9,
        }))
    assert json.loads(out) == {"success": True, "respuesta": "confirma"}
    client = factory.return_value.__aenter__.return_value
    url = client.post.call_args.args[0]
    assert url.endswith("/api/v1/whatsapp/conversations/9/appointments/respond")
    assert client.post.call_args.kwargs["json"] == {"respuesta": "confirma", "activity_id": 55}


def test_enviar_formulario_sin_cita_activa_no_envia():
    """cancelar/reprogramar con _sin_cita_activa=True no postea y pide ofrecer agendar."""
    with patch("app.core.langgraph.tools.whatsapp_flows.httpx.AsyncClient") as client_cls:
        out = _run(enviar_formulario.ainvoke({
            "flow": "reprogramar_cita", "conversation_id": 7, "_sin_cita_activa": True,
        }))
    data = json.loads(out)
    assert data["success"] is False
    assert data["error"] == "sin_cita_activa"
    assert "paciente_tercero" in data["instruccion"]
    client_cls.assert_not_called()


def test_responder_recordatorio_invalid_respuesta():
    """Una respuesta desconocida se rechaza local, sin tocar la red."""
    with patch("app.core.langgraph.tools.whatsapp_flows.httpx.AsyncClient") as client_cls:
        out = _run(responder_recordatorio.ainvoke({"respuesta": "quizas", "conversation_id": 1}))
    assert json.loads(out)["error"] == "invalid_respuesta"
    client_cls.assert_not_called()
