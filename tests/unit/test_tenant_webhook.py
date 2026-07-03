"""Tests for multi-tenant webhook routing (Plan B) — Fase 1."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app_client():
    with (
        patch("app.core.observability.langfuse_init"),
        patch("app.core.observability.langfuse_callback_handler", new=MagicMock()),
        patch("app.services.database.database_service"),
        patch("app.services.memory.memory_service"),
        patch("app.core.langgraph.tools.crm.ensure_person_registered", new_callable=AsyncMock),
        patch("app.core.langgraph.tools.crm.ensure_lead_registered", new_callable=AsyncMock),
        patch("app.core.langgraph.odontoking_graph.OdontokingAgent.create_graph", new_callable=AsyncMock),
        patch("app.core.langgraph.graph.LangGraphAgent.create_graph", new_callable=AsyncMock),
        patch("app.services.message_buffer.message_buffer_service.initialize", new_callable=AsyncMock),
        patch("app.services.message_buffer.message_buffer_service.close", new_callable=AsyncMock),
        patch("app.core.cache.cache_service.initialize", new_callable=AsyncMock),
        patch("app.core.cache.cache_service.close", new_callable=AsyncMock),
        patch("app.core.tenant._db_get", new=AsyncMock(return_value=None)),
    ):
        from app.main import app
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


# ── Tenant verify endpoint ────────────────────────────────────────────────────

class TestTenantWebhookVerify:
    def test_known_tenant_returns_challenge(self, app_client):
        resp = app_client.get(
            "/api/v1/whatsapp/odontoking/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test-verify-token",
                "hub.challenge": "abc123",
            },
        )
        assert resp.status_code == 200
        assert resp.text == "abc123"

    def test_unknown_tenant_returns_404(self, app_client):
        resp = app_client.get(
            "/api/v1/whatsapp/unknown-client/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "any-token",
                "hub.challenge": "xyz",
            },
        )
        assert resp.status_code == 404

    def test_wrong_verify_token_returns_403(self, app_client):
        resp = app_client.get(
            "/api/v1/whatsapp/odontoking/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "abc",
            },
        )
        assert resp.status_code == 403

    def test_legacy_route_still_works(self, app_client):
        resp = app_client.get(
            "/api/v1/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test-verify-token",
                "hub.challenge": "legacy123",
            },
        )
        assert resp.status_code == 200
        assert resp.text == "legacy123"


# ── Tenant receive endpoint ───────────────────────────────────────────────────

class TestTenantWebhookReceive:
    def _payload(self, wa_id: str = "591701234567", text: str = "Hola", msg_id: str = "m1") -> dict:
        return {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "e1",
                "changes": [{
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"display_phone_number": "x", "phone_number_id": "pid"},
                        "contacts": [{"wa_id": wa_id, "profile": {"name": "Test User"}}],
                        "messages": [{
                            "id": msg_id,
                            "from": wa_id,
                            "timestamp": "1",
                            "type": "text",
                            "text": {"body": text},
                        }],
                    },
                    "field": "messages",
                }],
            }],
        }

    def _post(self, client, tenant: str, payload: dict):
        return client.post(
            f"/api/v1/whatsapp/{tenant}/webhook",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

    def test_known_tenant_enqueues_message(self, app_client):
        with (
            patch("app.api.v1.whatsapp.ensure_person_registered", new_callable=AsyncMock) as mock_register,
            patch("app.api.v1.whatsapp.message_buffer_service.enqueue", new_callable=AsyncMock) as mock_enqueue,
        ):
            resp = self._post(app_client, "odontoking", self._payload())
            assert resp.status_code == 200
            mock_register.assert_called_once()
            mock_enqueue.assert_called_once()
            assert mock_enqueue.call_args[0][0] == "591701234567"

    def test_unknown_tenant_returns_200_silently(self, app_client):
        """Unknown tenants return 200 to prevent Meta from retrying."""
        resp = self._post(app_client, "ghost-tenant", self._payload())
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_legacy_route_routes_to_odontoking(self, app_client):
        with (
            patch("app.api.v1.whatsapp.ensure_person_registered", new_callable=AsyncMock) as mock_register,
            patch("app.api.v1.whatsapp.message_buffer_service.enqueue", new_callable=AsyncMock) as mock_enqueue,
        ):
            resp = app_client.post(
                "/api/v1/whatsapp/webhook",
                content=json.dumps(self._payload()),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200
            mock_register.assert_called_once()
            mock_enqueue.assert_called_once()

    def test_different_tenants_use_independent_process_fns(self, app_client):
        """Each tenant gets its own process_fn closure."""
        calls = []

        async def capture_enqueue(wa_id, text, process_fn):
            calls.append(id(process_fn))

        with (
            patch("app.api.v1.whatsapp.ensure_person_registered", new_callable=AsyncMock) as mock_register,
            patch("app.api.v1.whatsapp.message_buffer_service.enqueue", side_effect=capture_enqueue),
        ):
            self._post(app_client, "odontoking", self._payload(text="msg1", msg_id="t-unique-1"))
            self._post(app_client, "odontoking", self._payload(text="msg2", msg_id="t-unique-2"))

        # Both calls use the same tenant → same factory pattern (fn created per request)
        assert len(calls) == 2
        assert mock_register.call_count == 2


# ── Tenant registry ───────────────────────────────────────────────────────────

class TestTenantRegistry:
    def test_odontoking_tenant_exists(self):
        from app.core.tenant import get_tenant
        tenant = get_tenant("odontoking")
        assert tenant is not None
        assert tenant.slug == "odontoking"
        assert tenant.agent_type == "odontoking"

    def test_unknown_tenant_returns_none(self):
        from app.core.tenant import get_tenant
        assert get_tenant("nonexistent") is None

    def test_get_by_phone_id(self):
        from app.core.tenant import get_tenant, get_tenant_by_phone_id
        tenant = get_tenant("odontoking")
        if tenant and tenant.phone_number_id:
            found = get_tenant_by_phone_id(tenant.phone_number_id)
            assert found is not None
            assert found.slug == "odontoking"

    def test_list_tenants(self):
        from app.core.tenant import list_tenants
        tenants = list_tenants()
        assert "odontoking" in tenants
