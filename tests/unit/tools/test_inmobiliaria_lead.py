"""Unit tests for the eager lead-open on a new Ponce de León conversation.

abrir_lead_inicial opens the conversation's lead from message 1 (deterministic, not LLM-driven),
POSTing just the contact name to the conversation-scoped solicitud UPSERT endpoint.
"""

import pytest

from app.core.langgraph.tools import inmobiliaria
from app.core.langgraph.tools.inmobiliaria import abrir_lead_inicial


def _patch_httpx(monkeypatch, *, status=200, payload=None):
    """Patch inmobiliaria.httpx.AsyncClient with a fake recording the request; return the capture."""
    box = {"url": None, "json": None, "method": None}

    class _Resp:
        status_code = status
        content = b"{}"

        def json(self):
            return payload if payload is not None else {"message": "Lead creado.", "lead_id": 50}

        def raise_for_status(self):
            if status >= 400:
                import httpx

                raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, json=None, **k):
            box["method"], box["url"], box["json"] = method, url, json
            return _Resp()

    monkeypatch.setattr(inmobiliaria.httpx, "AsyncClient", _Client)
    return box


class TestAbrirLeadInicial:
    """Opens the lead with the contact name on the first message; UPSERT per conversation."""

    @pytest.mark.asyncio
    async def test_posts_name_to_solicitud_and_returns_lead_id(self, monkeypatch):
        """Uses the context name and POSTs to /inmobiliaria/conversations/{id}/solicitud."""
        box = _patch_httpx(monkeypatch, payload={"lead_id": 50})
        lead_id = await abrir_lead_inicial(
            {"metadata": {"conversation_id": 245, "nombre_whatsapp": "Alejandro"}}
        )
        assert lead_id == 50
        assert box["method"] == "POST"
        assert box["url"].endswith("/api/v1/inmobiliaria/conversations/245/solicitud")
        assert box["json"]["nombre"] == "Alejandro"

    @pytest.mark.asyncio
    async def test_falls_back_to_placeholder_name(self, monkeypatch):
        """With no contact name, a placeholder is sent (the CRM requires `nombre`)."""
        box = _patch_httpx(monkeypatch)
        await abrir_lead_inicial({"metadata": {"conversation_id": 245}})
        assert box["json"]["nombre"] == "Cliente WhatsApp"

    @pytest.mark.asyncio
    async def test_no_conversation_id_is_a_noop(self, monkeypatch):
        """No conversation_id → no POST, returns None (nothing to open)."""
        box = _patch_httpx(monkeypatch)
        lead_id = await abrir_lead_inicial({"metadata": {}})
        assert lead_id is None
        assert box["url"] is None

    @pytest.mark.asyncio
    async def test_crm_error_is_swallowed(self, monkeypatch):
        """A CRM failure never raises (must not break the reply); returns None."""
        _patch_httpx(monkeypatch, status=500)
        lead_id = await abrir_lead_inicial(
            {"metadata": {"conversation_id": 245, "nombre_whatsapp": "Ana"}}
        )
        assert lead_id is None
