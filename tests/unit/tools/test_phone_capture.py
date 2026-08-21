"""Unit tests for the Messenger phone-capture flow (integracion §Messenger).

Covers: schema field parsing/compat, the code-owned ask gate, the contact-phone submit helper
(happy path / 422 / 404 / 401 / refusal / empty guard), and the tool's response mapping.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.langgraph.tools import crm
from app.core.langgraph.tools.crm import (
    _ctx_conversation_id,
    _phone_ask_allowed,
    guardar_telefono_contacto,
    submit_contact_phone,
)
from app.schemas.crm import CrmContact, CrmWebhookEvent

_MESSENGER_EVENT = {
    "event": "message.received",
    "conversation_id": 1487,
    "gateway": "messenger",
    "ai_enabled": True,
    "contact": {
        "phone": None, "channel": "messenger", "name": "Ana Pérez", "person_id": 412, "lead_id": 890,
        "phone_required": True, "phone_prompt_state": "pending",
        "phone_prompt_attempts": 0, "phone_prompt_exhausted": False,
    },
    "message": {"id": 90211, "type": "text", "text": "cuánto sale un banner?", "timestamp": None},
    "reply": {"method": "POST", "url": "https://x/api/v1/whatsapp/conversations/1487/messages"},
}


def _http_error(status: int) -> httpx.HTTPStatusError:
    resp = MagicMock()
    resp.status_code = status
    return httpx.HTTPStatusError(str(status), request=MagicMock(), response=resp)


class TestSchemaCompat:
    """The new phone-capture fields parse, and their ABSENCE reads as 'don't ask' (§4)."""

    def test_full_messenger_payload_parses(self):
        """A messenger event with all phone fields populates the contact."""
        ev = CrmWebhookEvent.model_validate(_MESSENGER_EVENT)
        assert ev.contact.phone is None
        assert ev.contact.phone_required is True
        assert ev.contact.phone_prompt_state == "pending"
        assert ev.contact.phone_prompt_exhausted is False

    def test_absent_fields_default_to_not_required(self):
        """Old CRM (fields absent) → phone_required False, so the agent never asks."""
        c = CrmContact.model_validate({"channel": "cloud_api", "phone": "+59170012345"})
        assert c.phone_required is False
        assert c.phone_prompt_state is None
        assert c.phone_prompt_attempts == 0
        assert c.phone_prompt_exhausted is False


class TestPhoneAskGate:
    """_phone_ask_allowed is the hard, code-owned gate — the model only decides WHEN within it."""

    def test_allowed_when_required_pending_not_exhausted(self):
        """The canonical 'missing phone, never asked' case is askable."""
        assert _phone_ask_allowed(True, "pending", False) is True
        assert _phone_ask_allowed(True, "asked", False) is True

    def test_never_when_not_required(self):
        """phone_required False (or absent) → never ask (channel already has a phone)."""
        assert _phone_ask_allowed(False, "pending", False) is False
        assert _phone_ask_allowed(False, None, False) is False

    def test_never_when_exhausted(self):
        """phone_prompt_exhausted → stop asking, even if state still says pending."""
        assert _phone_ask_allowed(True, "pending", True) is False

    def test_never_when_captured_or_refused(self):
        """Already captured or refused → never ask again."""
        assert _phone_ask_allowed(True, "captured", False) is False
        assert _phone_ask_allowed(True, "refused", False) is False


class TestSubmitContactPhone:
    """submit_contact_phone maps every documented status and never raises."""

    @pytest.mark.asyncio
    async def test_happy_path_captured(self, monkeypatch):
        """200 with a captured number → status ok and the updated contact echoed back."""
        resp = MagicMock()
        resp.content = b"{}"
        resp.json.return_value = {"contact": {"phone": "+59170012345"}}
        monkeypatch.setattr(crm, "_request", AsyncMock(return_value=resp))
        out = await submit_contact_phone(1487, phone="+591 70012345")
        assert out["status"] == "ok"
        assert out["refused"] is False
        assert out["contact"] == {"phone": "+59170012345"}

    @pytest.mark.asyncio
    async def test_sends_number_verbatim(self, monkeypatch):
        """The number is forwarded exactly as dictated — no cleaning/formatting on our side (§3)."""
        req = AsyncMock(return_value=MagicMock(content=b"{}", json=MagicMock(return_value={})))
        monkeypatch.setattr(crm, "_request", req)
        await submit_contact_phone(1487, phone="setecientos NO, 700-12345")
        _, kwargs = req.call_args
        assert kwargs["json"] == {"phone": "setecientos NO, 700-12345", "source": "ai", "confidence": "stated"}

    @pytest.mark.asyncio
    async def test_422_invalid_number(self, monkeypatch):
        """422 → invalid; nothing saved, caller should re-ask if attempts remain."""
        monkeypatch.setattr(crm, "_request", AsyncMock(side_effect=_http_error(422)))
        out = await submit_contact_phone(1487, phone="abc")
        assert out["status"] == "invalid"
        assert out["http_status"] == 422

    @pytest.mark.asyncio
    async def test_404_and_401_are_terminal(self, monkeypatch):
        """404 (unknown conversation) and 401 (bad token) map to their own terminal statuses."""
        monkeypatch.setattr(crm, "_request", AsyncMock(side_effect=_http_error(404)))
        assert (await submit_contact_phone(9, phone="+59170012345"))["status"] == "not_found"
        monkeypatch.setattr(crm, "_request", AsyncMock(side_effect=_http_error(401)))
        assert (await submit_contact_phone(9, phone="+59170012345"))["status"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_refusal_sends_refused_payload(self, monkeypatch):
        """A refusal posts {refused: true} and never touches the phone field."""
        req = AsyncMock(return_value=MagicMock(content=b"{}", json=MagicMock(return_value={})))
        monkeypatch.setattr(crm, "_request", req)
        out = await submit_contact_phone(1487, refused=True)
        assert out["status"] == "ok" and out["refused"] is True
        assert req.call_args.kwargs["json"] == {"refused": True}

    @pytest.mark.asyncio
    async def test_empty_capture_is_guarded(self, monkeypatch):
        """An empty (non-refusal) capture is never POSTed — treated as invalid so the model re-asks."""
        req = AsyncMock()
        monkeypatch.setattr(crm, "_request", req)
        out = await submit_contact_phone(1487, phone="   ")
        assert out["status"] == "invalid"
        req.assert_not_called()


class TestPromptRendering:
    """The injected phone block renders only when the phone is required.

    It surfaces the code-evaluated ask gate as sí/no. The marker `telefono_conocido:` is unique to the
    rendered block, so it won't collide with the instructional copy in imprimir.md.
    """

    def test_block_rendered_with_gate_and_attempts_when_required(self):
        """A messenger, pending contact gets the block with puede_pedir=sí and the attempts count."""
        from app.core.langgraph.imprimir_graph import _load_imprimir_prompt

        p = _load_imprimir_prompt(
            "conv:1487", conversation_id=1487, channel="messenger",
            phone_prompt={"required": True, "state": "pending", "attempts": 1, "exhausted": False},
        )
        assert "telefono_conocido:" in p
        assert "puede_pedir_telefono: sí" in p
        assert "pedidos_hechos: 1 de 3" in p

    def test_gate_no_when_exhausted(self):
        """Exhausted → the block still renders but the gate reads no."""
        from app.core.langgraph.imprimir_graph import _load_imprimir_prompt

        p = _load_imprimir_prompt(
            "conv:1", conversation_id=1, channel="messenger",
            phone_prompt={"required": True, "state": "asked", "attempts": 3, "exhausted": True},
        )
        assert "telefono_conocido:" in p
        assert "puede_pedir_telefono: no" in p

    def test_no_block_when_not_required(self):
        """A channel that already has a phone (or old CRM) → no phone block at all."""
        from app.core.langgraph.imprimir_graph import _load_imprimir_prompt

        p = _load_imprimir_prompt(
            "59170012345", conversation_id=5, channel="cloud_api", phone_prompt={"required": False},
        )
        assert "telefono_conocido:" not in p


class TestGuardarTelefonoTool:
    """The tool reads conversation_id from config.metadata and shapes the result for the model."""

    def _cfg(self, conversation_id):
        return {"metadata": {"conversation_id": conversation_id}}

    def test_ctx_conversation_id(self):
        """conversation_id is read from injected metadata (LLM never passes it)."""
        assert _ctx_conversation_id(self._cfg(1487)) == 1487
        assert _ctx_conversation_id(None) is None

    @pytest.mark.asyncio
    async def test_tool_happy_path(self, monkeypatch):
        """A captured number returns ok without a re-ask flag."""
        monkeypatch.setattr(crm, "submit_contact_phone", AsyncMock(return_value={"status": "ok", "refused": False}))
        out = await guardar_telefono_contacto.ainvoke({"telefono": "70012345"}, self._cfg(1487))
        assert '"status": "ok"' in out
        assert "repreguntar" not in out

    @pytest.mark.asyncio
    async def test_tool_422_flags_reask(self, monkeypatch):
        """A 422 from the CRM adds repreguntar=true so the model asks once more."""
        monkeypatch.setattr(crm, "submit_contact_phone", AsyncMock(return_value={"status": "invalid", "http_status": 422}))
        out = await guardar_telefono_contacto.ainvoke({"telefono": "abc"}, self._cfg(1487))
        assert '"repreguntar": true' in out

    @pytest.mark.asyncio
    async def test_tool_without_conversation_id_errors(self):
        """No conversation_id in context → error, never a blind POST."""
        out = await guardar_telefono_contacto.ainvoke({"telefono": "70012345"}, {"metadata": {}})
        assert '"no_conversation_id"' in out
