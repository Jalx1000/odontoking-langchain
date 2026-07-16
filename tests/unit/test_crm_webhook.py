"""Unit tests for the sofo-crm inbound webhook (event parsing + agent-token auth)."""

from typing import cast

from fastapi import Request

from app.api.v1.crm import _verify_agent_token
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
