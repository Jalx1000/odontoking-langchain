"""Unit tests for the sofo-crm inbound webhook (event parsing + agent-token auth)."""

from typing import cast

from fastapi import Request

from app.api.v1.crm import _format_flow_reply, _verify_agent_token
from app.core.config import settings
from app.schemas.crm import CrmWebhookEvent

# The example event from integracion-gateway-whatsapp.md §3.
_DOC_EVENT = {
    "event": "message.received",
    "conversation_id": 5,
    "gateway": "kommo",
    "ai_enabled": True,
    "contact": {"phone": "+59176616013", "name": "Alejandro", "person_id": 7, "lead_id": None},
    "message": {"id": 164, "type": "text", "text": "hola, quiero cotizar", "timestamp": "2026-07-16T07:10:01-04:00"},
    "history": [
        {"role": "user", "content": "hola", "type": "text"},
        {"role": "assistant", "content": "¡Hola!", "type": "text"},
    ],
    "window": {"open": True, "expires_at": "2026-07-16T18:01:52-04:00"},
    "reply": {"method": "POST", "url": "https://imprimir.sofopolis.com/api/v1/whatsapp/conversations/5/messages"},
}


class _Req:
    """Minimal Request stub exposing a headers mapping."""

    def __init__(self, authorization: str | None):
        self.headers = {"authorization": authorization} if authorization is not None else {}


def _verify(authorization: str | None) -> bool:
    """Call _verify_agent_token with a stub Request (cast for the type checker)."""
    return _verify_agent_token(cast(Request, _Req(authorization)))


class TestCrmWebhookEvent:
    """CrmWebhookEvent parses the documented payload."""

    def test_parses_doc_example(self):
        """All key fields (contact, message, reply.url) are extracted."""
        ev = CrmWebhookEvent.model_validate(_DOC_EVENT)
        assert ev.event == "message.received"
        assert ev.conversation_id == 5
        assert ev.contact.phone == "+59176616013"
        assert ev.message.text == "hola, quiero cotizar"
        assert ev.reply is not None
        assert ev.reply.url.endswith("/conversations/5/messages")

    def test_tolerates_extra_fields(self):
        """Unknown fields are ignored (forward-compatible with CRM additions)."""
        payload = {**_DOC_EVENT, "unexpected": {"foo": "bar"}}
        ev = CrmWebhookEvent.model_validate(payload)
        assert ev.contact.name == "Alejandro"

    def test_parses_flow_reply_and_patient_context(self):
        """A flow_reply message and patient_context are parsed into structured fields."""
        payload = {
            **_DOC_EVENT,
            "message": {
                "id": 200,
                "type": "flow_reply",
                "flow": {
                    "flow": "paciente_tercero",
                    "status": "pending",
                    "patient_name": "Juan Pérez",
                    "relation": "hijo",
                    "date": "2026-10-15",
                    "time": "10:00",
                },
            },
            "patient_context": {
                "pacientes_a_cargo": [{"nombre": "Juan Pérez", "relacion": "hijo"}],
                "citas": [{"fecha": "2026-10-15", "hora": "10:00"}],
            },
        }
        ev = CrmWebhookEvent.model_validate(payload)
        assert ev.message.type == "flow_reply"
        assert ev.message.flow["status"] == "pending"
        assert ev.patient_context is not None
        assert ev.patient_context.pacientes_a_cargo[0]["relacion"] == "hijo"


class TestFormatFlowReply:
    """_format_flow_reply renders a form submission as a compact note for the LLM."""

    def test_flow_and_status_lead(self):
        """Flow and status are surfaced first; None values are dropped."""
        note = _format_flow_reply(
            {
                "patient_name": "Juan Pérez",
                "flow": "paciente_tercero",
                "status": "pending",
                "doctor_name": None,
                "date": "2026-10-15",
            }
        )
        lines = note.splitlines()
        assert lines[0] == "[FORMULARIO COMPLETADO POR EL PACIENTE]"
        assert lines[1] == "flow: paciente_tercero"
        assert lines[2] == "status: pending"
        assert "patient_name: Juan Pérez" in note
        assert "date: 2026-10-15" in note
        assert "doctor_name" not in note  # None values are omitted


class TestVerifyAgentToken:
    """_verify_agent_token compares the Bearer against WHATSAPP_AGENT_TOKEN."""

    def test_accepts_matching_bearer(self, monkeypatch):
        """A matching Bearer token is accepted."""
        monkeypatch.setattr(settings, "WHATSAPP_AGENT_TOKEN", "secret")
        assert _verify("Bearer secret") is True

    def test_rejects_wrong_bearer(self, monkeypatch):
        """A non-matching token is rejected."""
        monkeypatch.setattr(settings, "WHATSAPP_AGENT_TOKEN", "secret")
        assert _verify("Bearer nope") is False

    def test_rejects_missing_header(self, monkeypatch):
        """A missing Authorization header is rejected."""
        monkeypatch.setattr(settings, "WHATSAPP_AGENT_TOKEN", "secret")
        assert _verify(None) is False

    def test_rejects_non_bearer_scheme(self, monkeypatch):
        """A non-Bearer scheme is rejected."""
        monkeypatch.setattr(settings, "WHATSAPP_AGENT_TOKEN", "secret")
        assert _verify("Basic secret") is False

    def test_rejects_when_token_not_configured(self, monkeypatch):
        """With no WHATSAPP_AGENT_TOKEN configured, all requests are rejected."""
        monkeypatch.setattr(settings, "WHATSAPP_AGENT_TOKEN", "")
        assert _verify("Bearer anything") is False
