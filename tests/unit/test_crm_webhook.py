"""Unit tests for the sofo-crm inbound webhook (event parsing + agent-token auth)."""

import asyncio
import json
from typing import cast

from fastapi import Request

from app.api.v1 import crm
from app.api.v1.crm import _format_flow_reply, _is_first_contact, _mensajes_crm, _should_send_media_reply, _verify_agent_token
from app.core.config import settings
from app.schemas.crm import CrmHistoryItem, CrmWebhookEvent

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


class TestIsFirstContact:
    """_is_first_contact drives the welcome greeting (es_primer_contacto)."""

    def _hist(self, *roles: str) -> list[CrmHistoryItem]:
        return [CrmHistoryItem(role=r, content="x") for r in roles]

    def test_first_message_is_first_contact_even_though_crm_echoes_it(self):
        """REGRESSION: the CRM echoes the current user message into history.

        On the first message history is [user] (len 1), NOT empty, so the old `not history`
        check was always False and the welcome never fired. No assistant turn yet → first contact.
        """
        assert _is_first_contact(self._hist("user")) is True

    def test_two_buffered_user_messages_still_first_contact(self):
        """Prod case 59176616013: two buffered user messages, no reply yet → first contact.

        'Quiero agendar...' + 'En la tarde' arrive buffered, history is [user, user] with no
        assistant reply yet → still a first contact (greet).
        """
        assert _is_first_contact(self._hist("user", "user")) is True

    def test_not_first_contact_once_assistant_has_replied(self):
        """An assistant turn in history means the agent already greeted → no second welcome."""
        assert _is_first_contact(self._hist("user", "assistant", "user")) is False

    def test_empty_history_is_first_contact(self):
        """Defensive: if the CRM ever omits the echo, an empty history is still first contact."""
        assert _is_first_contact([]) is True


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


class TestMensajesCrm:
    """What the CRM or reception sent is passed to the agent as context."""

    def test_only_crm_and_reception_messages(self):
        """The agent's own turns and the patient's messages are already in its memory."""
        history = [
            CrmHistoryItem(role="user", content="hola", sender="contact"),
            CrmHistoryItem(role="assistant", content="soy el bot", sender="ia"),
            CrmHistoryItem(role="assistant", content="recordatorio", sender="system"),
            CrmHistoryItem(role="assistant", content="le escribe recepción", sender="human"),
        ]
        assert _mensajes_crm(history) == [
            {"origen": "sistema", "texto": "recordatorio"},
            {"origen": "recepción", "texto": "le escribe recepción"},
        ]

    def test_skips_empty_and_keeps_the_latest(self):
        """A template without text adds nothing, and only the last messages are kept."""
        history = [CrmHistoryItem(role="assistant", content=f"m{i}", sender="human") for i in range(5)]
        history.append(CrmHistoryItem(role="assistant", content="", sender="system"))
        assert [m["texto"] for m in _mensajes_crm(history)] == ["m2", "m3", "m4"]

    def test_older_payload_without_sender(self):
        """A CRM that does not send `sender` yet must not break the event."""
        assert _mensajes_crm([CrmHistoryItem(role="assistant", content="x")]) == []


class TestMediaReply:
    """A patient who sends an attachment is told the agent cannot read it, once."""

    def test_a_burst_gets_one_notice(self):
        """Several photos in a row produce a single notice per conversation."""
        crm._media_replied_at.clear()
        assert _should_send_media_reply(1) is True
        assert _should_send_media_reply(1) is False
        assert _should_send_media_reply(2) is True

    def test_notice_comes_back_after_the_ttl(self):
        """Once the window passes the patient is told again."""
        crm._media_replied_at.clear()
        crm._media_replied_at[1] = crm.time.monotonic() - crm._MEDIA_REPLY_TTL - 1
        assert _should_send_media_reply(1) is True

    def test_stickers_get_no_notice(self):
        """A sticker is not a question."""
        assert "sticker" not in crm._MEDIA_REPLIES


class _WebhookReq:
    """Request stub for calling the webhook handler directly."""

    def __init__(self, body: dict):
        self._raw = json.dumps(body).encode()
        self.headers: dict = {}

    async def body(self) -> bytes:
        return self._raw


class _Gateway:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, dest, text):
        self.sent.append(text)


def _deliver(monkeypatch, message: dict) -> tuple[_Gateway, list[str]]:
    """Run one event through the webhook; returns what was sent and what reached the agent."""
    gateway = _Gateway()
    processed: list[str] = []

    async def process(_phone, text):
        processed.append(text)

    crm._media_replied_at.clear()
    crm._seen_message_ids.clear()
    monkeypatch.setattr(crm.settings, "WHATSAPP_GATEWAY", "sofo-crm")
    monkeypatch.setattr(crm.settings, "WHATSAPP_AUTO_CREATE_PERSON", False)
    monkeypatch.setattr(crm.settings, "BUFFER_ENABLED", False)
    monkeypatch.setattr(crm, "_verify_agent_token", lambda _r: True)
    monkeypatch.setattr(crm, "get_gateway", lambda: gateway)
    monkeypatch.setattr(crm, "_make_process_fn", lambda *a, **k: process)

    async def run():
        await crm.receive_crm_event.__wrapped__(_WebhookReq({**_DOC_EVENT, "message": message}))
        await asyncio.gather(*crm._background_tasks)

    asyncio.run(run())
    return gateway, processed


class TestWebhookAttachments:
    """Attachments are read, and never vanish silently."""

    _MEDIA = {"url": "https://crm.test/whatsapp/media/a.jpg", "mime": "image/jpeg"}

    def _reader(self, monkeypatch, result):
        async def read(_type, _media):
            return result

        monkeypatch.setattr(crm, "read_attachment", read)

    def test_attachment_content_reaches_the_agent(self, monkeypatch):
        self._reader(monkeypatch, "[Imagen...]: carnet")
        gateway, processed = _deliver(monkeypatch, {"id": 1, "type": "image", "media": self._MEDIA})
        assert gateway.sent == []
        assert processed == ["[Imagen...]: carnet"]

    def test_caption_goes_with_the_content(self, monkeypatch):
        self._reader(monkeypatch, "[Imagen...]: carnet")
        _, processed = _deliver(monkeypatch, {"id": 2, "type": "image", "text": "mi seguro", "media": self._MEDIA})
        assert processed == ["mi seguro\n[Imagen...]: carnet"]

    def test_unreadable_attachment_gets_a_notice(self, monkeypatch):
        self._reader(monkeypatch, "")
        gateway, processed = _deliver(monkeypatch, {"id": 3, "type": "image", "media": self._MEDIA})
        assert gateway.sent == [crm._MEDIA_REPLIES["image"]]
        assert processed == []

    def test_unreadable_attachment_keeps_its_caption(self, monkeypatch):
        self._reader(monkeypatch, "")
        gateway, processed = _deliver(monkeypatch, {"id": 4, "type": "image", "text": "este", "media": self._MEDIA})
        assert gateway.sent == []
        assert processed == ["este"]

    def test_not_stored_by_the_crm_gets_a_notice(self, monkeypatch):
        gateway, processed = _deliver(monkeypatch, {"id": 5, "type": "audio", "text": None})
        assert gateway.sent == [crm._MEDIA_REPLIES["audio"]]
        assert processed == []

    def test_video_is_not_read(self, monkeypatch):
        gateway, _ = _deliver(monkeypatch, {"id": 6, "type": "video", "media": self._MEDIA})
        assert gateway.sent == [crm._MEDIA_REPLIES["video"]]

    def test_sticker_stays_ignored(self, monkeypatch):
        gateway, processed = _deliver(monkeypatch, {"id": 7, "type": "sticker", "text": None})
        assert gateway.sent == []
        assert processed == []
