"""Sprint 4 — webhook routing tests.

These tests validate that the WhatsApp webhook routes incoming messages
based on the tenant's `agent_endpoint_url`:

  * Set        → publish to RabbitMQ via `broker.publish`. No internal buffer.
  * Empty/None → keep current behaviour: enqueue into `message_buffer_service`.

The published contract is exactly: `broker.publish(slug, wa_id, {"text": ..., "message_id": ...})`.
"""

import os
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure required env is set before any app import (mirrors test_internal.py)
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key-abc123")


def _make_tenant(*, slug: str = "odontoking", agent_endpoint_url: Optional[str] = None) -> Any:
    """Build a TenantConfig-compatible stub for the webhook handler."""
    from app.core.tenant import TenantConfig

    return TenantConfig(
        slug=slug,
        display_name="Odontoking",
        verify_token="verify-tok",
        phone_number_id="pid-123",
        wa_access_token="wa-tok",
        agent_type="odontoking",
        crm_url="https://crm.test",
        crm_token="crm-tok",
        agent_endpoint_url=agent_endpoint_url,
        agent_api_key=None,
        llm_model="gpt-4o-mini",
        billing_model="fixed",
        billing_tier="local",
    )


@pytest.fixture(scope="module")
def app_client():
    """Create a FastAPI test client with all external dependencies mocked."""
    with (
        patch("app.core.observability.langfuse_init"),
        patch("app.core.observability.langfuse_callback_handler", new=MagicMock()),
        patch("app.services.database.database_service"),
        patch("app.services.memory.memory_service"),
        patch("app.core.langgraph.odontoking_graph.OdontokingAgent.create_graph"),
        patch("app.core.langgraph.graph.LangGraphAgent.create_graph"),
        patch("app.services.message_buffer.message_buffer_service.initialize"),
        patch("app.services.message_buffer.message_buffer_service.close"),
        patch("app.core.cache.cache_service.initialize"),
        patch("app.core.cache.cache_service.close"),
    ):
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


@pytest.fixture
def patched_routing():
    """Patch the seams the webhook touches when routing a text message.

    Returns the mock objects so each test can inspect call args.
    """
    publish_mock = AsyncMock()
    enqueue_mock = AsyncMock()
    mark_read_mock = AsyncMock()

    with (
        patch("app.api.v1.whatsapp.broker.publish", publish_mock),
        patch("app.api.v1.whatsapp.message_buffer_service.enqueue", enqueue_mock),
        patch("app.api.v1.whatsapp.mark_as_read", mark_read_mock),
    ):
        yield {
            "publish": publish_mock,
            "enqueue": enqueue_mock,
            "mark_read": mark_read_mock,
        }


class TestSprint4Routing:
    """POST /api/v1/whatsapp/{slug}/webhook routes by tenant.agent_endpoint_url."""

    def test_external_tenant_publishes_to_broker(
        self,
        app_client: TestClient,
        patched_routing: dict,
        sample_whatsapp_text_payload: dict,
        wa_id: str,
    ):
        """Tenant with agent_endpoint_url → publishes to broker, skips internal buffer."""
        tenant = _make_tenant(agent_endpoint_url="https://agent.example/webhook")

        with patch(
            "app.api.v1.whatsapp.get_tenant_async",
            new=AsyncMock(return_value=tenant),
        ):
            response = app_client.post(
                "/api/v1/whatsapp/odontoking/webhook",
                json=sample_whatsapp_text_payload,
            )

        assert response.status_code == 200
        assert patched_routing["publish"].await_count == 1
        assert patched_routing["enqueue"].await_count == 0

        args, _kwargs = patched_routing["publish"].call_args
        assert args[0] == tenant.slug
        assert args[1] == wa_id
        assert args[2] == {"text": "Hola, necesito un turno", "message_id": "msg-id-1"}

    def test_internal_tenant_uses_buffer(
        self,
        app_client: TestClient,
        patched_routing: dict,
        sample_whatsapp_text_payload: dict,
        wa_id: str,
    ):
        """Tenant without agent_endpoint_url → enqueues into local buffer, no broker publish."""
        tenant = _make_tenant(agent_endpoint_url=None)

        with patch(
            "app.api.v1.whatsapp.get_tenant_async",
            new=AsyncMock(return_value=tenant),
        ):
            response = app_client.post(
                "/api/v1/whatsapp/odontoking/webhook",
                json=sample_whatsapp_text_payload,
            )

        assert response.status_code == 200
        assert patched_routing["publish"].await_count == 0
        assert patched_routing["enqueue"].await_count == 1

        args, _kwargs = patched_routing["enqueue"].call_args
        # Positional args contract for buffer.enqueue is wa_id, text, process_fn.
        assert args[0] == wa_id
        assert args[1] == "Hola, necesito un turno"

    def test_published_message_contract(
        self,
        app_client: TestClient,
        patched_routing: dict,
        sample_whatsapp_text_payload: dict,
        wa_id: str,
    ):
        """The dict passed to broker.publish must contain exactly text and message_id."""
        tenant = _make_tenant(agent_endpoint_url="https://agent.example/webhook")

        with patch(
            "app.api.v1.whatsapp.get_tenant_async",
            new=AsyncMock(return_value=tenant),
        ):
            response = app_client.post(
                "/api/v1/whatsapp/odontoking/webhook",
                json=sample_whatsapp_text_payload,
            )

        assert response.status_code == 200
        args, _kwargs = patched_routing["publish"].call_args
        payload = args[2]

        # Exact keys — no extras, no missing.
        assert set(payload.keys()) == {"text", "message_id"}
        assert isinstance(payload["text"], str) and payload["text"]
        assert isinstance(payload["message_id"], str) and payload["message_id"]

    def test_external_tenant_publish_failure_returns_200(
        self,
        app_client: TestClient,
        patched_routing: dict,
        sample_whatsapp_text_payload: dict,
    ):
        """Broker errors are swallowed so the webhook still returns 200 to Meta."""
        tenant = _make_tenant(agent_endpoint_url="https://agent.example/webhook")
        patched_routing["publish"].side_effect = RuntimeError("rabbitmq is down")

        with patch(
            "app.api.v1.whatsapp.get_tenant_async",
            new=AsyncMock(return_value=tenant),
        ):
            response = app_client.post(
                "/api/v1/whatsapp/odontoking/webhook",
                json=sample_whatsapp_text_payload,
            )

        assert response.status_code == 200
        # internal path must not be used as a fallback — that would split-brain the routing
        assert patched_routing["enqueue"].await_count == 0
