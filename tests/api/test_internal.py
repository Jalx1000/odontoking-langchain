"""Tests for POST /api/v1/internal/usage endpoint."""

import os
import uuid
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set required env vars before any app import
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key-abc123")

_VALID_KEY = "test-internal-key-abc123"
_ENDPOINT = "/api/v1/internal/usage"


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


def _make_tenant() -> MagicMock:
    t = MagicMock()
    t.id = uuid.uuid4()
    t.is_active = True
    return t


def _make_usage_log(tenant_id: uuid.UUID) -> MagicMock:
    log = MagicMock()
    log.tenant_id = tenant_id
    log.log_date = date.today()
    log.msg_processed = 0
    log.tokens_input = 0
    log.tokens_output = 0
    log.cost_usd = 0.0
    return log


class TestInternalUsageEndpoint:
    """Tests for POST /api/v1/internal/usage."""

    def test_valid_key_and_tenant_returns_200(self, app_client: TestClient):
        """Valid key + known tenant → 200 {"status": "ok"}."""
        tenant = _make_tenant()
        usage_log = _make_usage_log(tenant.id)

        session_mock = MagicMock()
        session_mock.__enter__ = MagicMock(return_value=session_mock)
        session_mock.__exit__ = MagicMock(return_value=False)
        session_mock.exec.return_value.first.side_effect = [tenant, usage_log]

        with patch("app.api.v1.internal.Session", return_value=session_mock):
            response = app_client.post(
                _ENDPOINT,
                json={
                    "tenant_slug": "odontoking",
                    "wa_id": "591701234567",
                    "tokens_input": 100,
                    "tokens_output": 50,
                    "cost_usd": 0.002,
                    "msg_processed": 1,
                },
                headers={"X-Internal-Key": _VALID_KEY},
            )

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_invalid_key_returns_401(self, app_client: TestClient):
        """Wrong X-Internal-Key → 401."""
        response = app_client.post(
            _ENDPOINT,
            json={
                "tenant_slug": "odontoking",
                "wa_id": "591701234567",
            },
            headers={"X-Internal-Key": "wrong-key"},
        )
        assert response.status_code == 401

    def test_missing_key_header_returns_401(self, app_client: TestClient):
        """No X-Internal-Key header at all → 401."""
        response = app_client.post(
            _ENDPOINT,
            json={
                "tenant_slug": "odontoking",
                "wa_id": "591701234567",
            },
            # deliberately no headers= — Header(default="") produces empty string, rejected
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid internal API key."

    def test_valid_key_unknown_tenant_returns_404(self, app_client: TestClient):
        """Valid key but tenant not found in DB → 404."""
        session_mock = MagicMock()
        session_mock.__enter__ = MagicMock(return_value=session_mock)
        session_mock.__exit__ = MagicMock(return_value=False)
        session_mock.exec.return_value.first.return_value = None

        with patch("app.api.v1.internal.Session", return_value=session_mock):
            response = app_client.post(
                _ENDPOINT,
                json={
                    "tenant_slug": "nonexistent-tenant",
                    "wa_id": "591701234567",
                },
                headers={"X-Internal-Key": _VALID_KEY},
            )

        assert response.status_code == 404
        assert response.json()["detail"] == "Tenant not found."

    def test_second_call_increments_existing_row(self, app_client: TestClient):
        """Second call with same tenant/date → increments counters, no duplicate row created."""
        tenant = _make_tenant()
        # Start with an existing UsageLog that already has some counts
        existing_log = _make_usage_log(tenant.id)
        existing_log.msg_processed = 3
        existing_log.tokens_input = 300
        existing_log.tokens_output = 150
        existing_log.cost_usd = 0.006

        session_mock = MagicMock()
        session_mock.__enter__ = MagicMock(return_value=session_mock)
        session_mock.__exit__ = MagicMock(return_value=False)
        # First exec → tenant lookup; second exec → existing UsageLog row
        session_mock.exec.return_value.first.side_effect = [tenant, existing_log]

        with patch("app.api.v1.internal.Session", return_value=session_mock):
            response = app_client.post(
                _ENDPOINT,
                json={
                    "tenant_slug": "odontoking",
                    "wa_id": "591701234567",
                    "tokens_input": 100,
                    "tokens_output": 50,
                    "cost_usd": 0.002,
                    "msg_processed": 1,
                },
                headers={"X-Internal-Key": _VALID_KEY},
            )

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        # The existing row must have been mutated in-place, not a new one added
        session_mock.add.assert_not_called()  # no new row inserted
        assert existing_log.msg_processed == 4       # 3 + 1
        assert existing_log.tokens_input == 400      # 300 + 100
        assert existing_log.tokens_output == 200     # 150 + 50
        assert round(existing_log.cost_usd, 6) == round(0.006 + 0.002, 6)
        session_mock.commit.assert_called_once()
