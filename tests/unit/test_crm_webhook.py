"""Unit tests for the sofo-crm inbound webhook (event parsing + agent-token auth)."""

from typing import cast

from fastapi import Request

from app.api.v1.crm import _extract_agent_text, _verify_agent_token
from app.core.config import settings
from app.schemas.crm import CrmMessage, CrmSelection, CrmWebhookEvent

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

    def test_null_phone_does_not_reject_event(self):
        """messenger/instagram never carry a phone — a null must not lose the message (the prod bug)."""
        payload = {**_DOC_EVENT, "gateway": "messenger",
                   "contact": {"phone": None, "channel": "messenger", "name": "Ale"}}
        ev = CrmWebhookEvent.model_validate(payload)
        assert ev.contact.phone is None

    def test_null_message_text_does_not_reject_event(self):
        """audio/sticker/location carry no text — a null message.text must parse, not crash."""
        payload = {**_DOC_EVENT, "message": {"id": 9, "type": "audio", "text": None, "timestamp": None}}
        ev = CrmWebhookEvent.model_validate(payload)
        assert ev.message.text is None

    def test_null_history_content_coerced_to_empty(self):
        """Non-text history items send content: null; it is coerced to '' so the event survives."""
        payload = {**_DOC_EVENT, "history": [{"role": "user", "content": None, "type": "audio"}]}
        ev = CrmWebhookEvent.model_validate(payload)
        assert ev.history[0].content == ""

    def test_unknown_gateway_does_not_reject_event(self):
        """The gateway is an open str, not a closed enum: a future channel must never break parsing."""
        payload = {**_DOC_EVENT, "gateway": "instagram"}
        ev = CrmWebhookEvent.model_validate(payload)
        assert ev.gateway == "instagram"


class TestInteractiveReply:
    """A button/list tap must reach the agent, routed by selection.id (never the title)."""

    _TAP = {
        "id": 987,
        "type": "interactive",
        "text": "🟢 Bolsas Magia Verde",
        "selection": {"id": "prod_bolsas", "title": "🟢 Bolsas Magia Verde"},
        "timestamp": "2026-09-09T17:40:12-04:00",
    }

    def test_selection_parses(self):
        """The interactive payload parses and exposes selection.id."""
        ev = CrmWebhookEvent.model_validate({**_DOC_EVENT, "message": self._TAP})
        assert ev.message.type == "interactive"
        assert ev.message.selection is not None
        assert ev.message.selection.id == "prod_bolsas"

    def test_tap_is_not_dropped_and_routes_by_id(self):
        """The tap yields agent text (not ignored) and that text carries the selection.id."""
        text = _extract_agent_text(
            CrmMessage(type="interactive", text="🟢 Bolsas Magia Verde",
                       selection=CrmSelection(id="prod_bolsas", title="🟢 Bolsas Magia Verde"))
        )
        assert text is not None            # NOT dropped (this was the prod bug)
        assert "prod_bolsas" in text       # routes by id

    def test_editing_the_title_does_not_change_the_routing_key(self):
        """Button copy is unstable; the id is the contract. Same id → same routing key."""
        a = _extract_agent_text(CrmMessage(type="interactive", text="A", selection=CrmSelection(id="prod_bolsas", title="A")))
        b = _extract_agent_text(CrmMessage(type="interactive", text="B", selection=CrmSelection(id="prod_bolsas", title="B")))
        assert a is not None and b is not None
        assert "prod_bolsas" in a and "prod_bolsas" in b

    def test_plain_text_still_extracted(self):
        """A normal text message is unaffected."""
        assert _extract_agent_text(CrmMessage(type="text", text="hola")) == "hola"

    def test_unsupported_type_ignored(self):
        """audio/image/location have no agent text yet → ignored (separate follow-up)."""
        assert _extract_agent_text(CrmMessage(type="audio", text=None)) is None

    def test_interactive_without_usable_id_ignored(self):
        """Defensive: interactive without a selection.id is ignored — never routed by the title."""
        assert _extract_agent_text(CrmMessage(type="interactive", text="hola", selection=None)) is None


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
