"""Unit tests for the outbound WhatsApp gateway (Meta vs sofo-crm)."""

import httpx
import pytest

import app.services.gateway as gateway_mod
from app.core.config import settings
from app.services.gateway import Destination, get_gateway
from app.services.gateway.crm import CrmGateway
from app.services.gateway.meta import MetaGateway


def _mock_crm_client(monkeypatch, handler):
    """Route CrmGateway's httpx client through a MockTransport handler."""
    from app.services.gateway import crm as crm_mod

    real_client = httpx.AsyncClient  # capture before patching to avoid recursion

    def factory(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(crm_mod.httpx, "AsyncClient", factory)


class TestGatewaySelection:
    """get_gateway() honours settings.WHATSAPP_GATEWAY."""

    def test_defaults_to_meta(self, monkeypatch):
        """Unset / 'meta' selects the Meta Cloud API gateway."""
        monkeypatch.setattr(settings, "WHATSAPP_GATEWAY", "meta")
        monkeypatch.setattr(gateway_mod, "_gateway", None)
        assert isinstance(get_gateway(), MetaGateway)
        assert get_gateway().name == "meta"

    def test_selects_crm(self, monkeypatch):
        """'sofo-crm' selects the CRM middleware gateway."""
        monkeypatch.setattr(settings, "WHATSAPP_GATEWAY", "sofo-crm")
        monkeypatch.setattr(gateway_mod, "_gateway", None)
        assert isinstance(get_gateway(), CrmGateway)
        assert get_gateway().name == "sofo-crm"


class TestCrmGateway:
    """CrmGateway posts replies to the CRM conversation endpoint."""

    @pytest.mark.asyncio
    async def test_send_response_posts_to_reply_url(self, monkeypatch):
        """POST goes to reply_url with the Sanctum bearer and Markdown stripped."""
        monkeypatch.setattr(settings, "CRM_API_KEY", "test-sanctum")
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = request.content.decode()
            return httpx.Response(200, json={"message": {"id": 1, "status": "queued", "sender": "ia"}})

        _mock_crm_client(monkeypatch, handler)
        dest = Destination(
            wa_id="+59176616013",
            conversation_id=5,
            reply_url="https://imprimir.sofopolis.com/api/v1/whatsapp/conversations/5/messages",
        )
        await CrmGateway().send_response(dest, "Hola **Alejandro**")

        assert captured["url"].endswith("/conversations/5/messages")
        assert captured["auth"] == "Bearer test-sanctum"
        assert "**" not in captured["body"]
        assert "Hola Alejandro" in captured["body"]

    @pytest.mark.asyncio
    async def test_falls_back_to_conversation_id_url(self, monkeypatch):
        """When reply_url is absent, the URL is built from CRM_BASE_URL + conversation_id."""
        monkeypatch.setattr(settings, "CRM_API_KEY", "k")
        monkeypatch.setattr(settings, "CRM_BASE_URL", "https://crm.test")
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={})

        _mock_crm_client(monkeypatch, handler)
        await CrmGateway().send_text(Destination(wa_id="+591700", conversation_id=9), "hola")
        assert captured["url"] == "https://crm.test/api/v1/whatsapp/conversations/9/messages"

    @pytest.mark.asyncio
    async def test_422_does_not_raise(self, monkeypatch):
        """A 422 (outside the 24h window) is logged and swallowed, never retried/raised."""
        monkeypatch.setattr(settings, "CRM_API_KEY", "k")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"message": "fuera de la ventana de 24h"})

        _mock_crm_client(monkeypatch, handler)
        # Must not raise.
        await CrmGateway().send_text(Destination(wa_id="+591700", reply_url="https://crm.test/x"), "hola")

    @pytest.mark.asyncio
    async def test_server_error_raises(self, monkeypatch):
        """Non-422 errors propagate (so callers can log/handle)."""
        monkeypatch.setattr(settings, "CRM_API_KEY", "k")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"message": "boom"})

        _mock_crm_client(monkeypatch, handler)
        with pytest.raises(httpx.HTTPStatusError):
            await CrmGateway().send_text(Destination(wa_id="+591700", reply_url="https://crm.test/x"), "hola")

    @pytest.mark.asyncio
    async def test_typing_and_mark_read_are_noops(self, monkeypatch):
        """The CRM has no typing / read-receipt API, so those are no-ops (no HTTP call)."""
        called = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200)

        _mock_crm_client(monkeypatch, handler)
        dest = Destination(wa_id="+591700", reply_url="https://crm.test/x")
        await CrmGateway().send_typing(dest)
        await CrmGateway().mark_read(dest, "msg-1")
        assert called["n"] == 0
