"""Integration-style tests for the WhatsApp webhook endpoint (no real DB/LLM)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app_client():
    """Create a FastAPI test client with all external dependencies mocked."""
    # Patch heavy startup deps before importing the app
    with (
        patch("app.core.observability.langfuse_init"),
        patch("app.core.observability.langfuse_callback_handler", new=MagicMock()),
        patch("app.services.database.database_service"),
        patch("app.services.memory.memory_service"),
        patch("app.core.langgraph.odontoking_graph.OdontokingAgent.create_graph", new_callable=AsyncMock),
        patch("app.core.langgraph.graph.LangGraphAgent.create_graph", new_callable=AsyncMock),
        patch("app.services.message_buffer.message_buffer_service.initialize", new_callable=AsyncMock),
        patch("app.services.message_buffer.message_buffer_service.close", new_callable=AsyncMock),
        patch("app.core.cache.cache_service.initialize", new_callable=AsyncMock),
        patch("app.core.cache.cache_service.close", new_callable=AsyncMock),
    ):
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


class TestWebhookVerification:
    def test_valid_token_returns_challenge(self, app_client):
        resp = app_client.get(
            "/api/v1/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test-verify-token",
                "hub.challenge": "challenge-abc",
            },
        )
        assert resp.status_code == 200
        assert resp.text == "challenge-abc"

    def test_invalid_token_returns_403(self, app_client):
        resp = app_client.get(
            "/api/v1/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge-abc",
            },
        )
        assert resp.status_code == 403

    def test_wrong_mode_returns_403(self, app_client):
        resp = app_client.get(
            "/api/v1/whatsapp/webhook",
            params={
                "hub.mode": "unsubscribe",
                "hub.verify_token": "test-verify-token",
                "hub.challenge": "challenge-abc",
            },
        )
        assert resp.status_code == 403


class TestWebhookReceiveMessage:
    def _post(self, app_client, payload: dict) -> MagicMock:
        return app_client.post(
            "/api/v1/whatsapp/webhook",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

    def test_status_update_only_returns_ok(self, app_client, sample_whatsapp_status_payload):
        with patch("app.api.v1.whatsapp.message_buffer_service.enqueue", new_callable=AsyncMock) as mock_enqueue:
            resp = self._post(app_client, sample_whatsapp_status_payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
            mock_enqueue.assert_not_called()

    def test_text_message_is_enqueued(self, app_client, sample_whatsapp_text_payload):
        with patch("app.api.v1.whatsapp.message_buffer_service.enqueue", new_callable=AsyncMock) as mock_enqueue:
            resp = self._post(app_client, sample_whatsapp_text_payload)
            assert resp.status_code == 200
            mock_enqueue.assert_called_once()
            call_args = mock_enqueue.call_args
            assert call_args[0][0] == "591701234567"
            assert call_args[0][1] == "Hola, necesito un turno"

    def test_audio_message_triggers_transcription_and_enqueue(self, app_client, sample_whatsapp_audio_payload):
        with (
            patch("app.api.v1.whatsapp.download_media", new_callable=AsyncMock, return_value=b"fake-bytes"),
            patch("app.api.v1.whatsapp.transcribe_audio", new_callable=AsyncMock, return_value="quiero un turno"),
            patch("app.api.v1.whatsapp.message_buffer_service.enqueue", new_callable=AsyncMock) as mock_enqueue,
        ):
            resp = self._post(app_client, sample_whatsapp_audio_payload)
            assert resp.status_code == 200
            mock_enqueue.assert_called_once()
            assert mock_enqueue.call_args[0][1] == "quiero un turno"

    def test_empty_audio_transcription_sends_error_reply(self, app_client, sample_whatsapp_audio_payload):
        with (
            patch("app.api.v1.whatsapp.download_media", new_callable=AsyncMock, return_value=b"bytes"),
            patch("app.api.v1.whatsapp.transcribe_audio", new_callable=AsyncMock, return_value=""),
            patch("app.api.v1.whatsapp.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("app.api.v1.whatsapp.message_buffer_service.enqueue", new_callable=AsyncMock) as mock_enqueue,
        ):
            resp = self._post(app_client, sample_whatsapp_audio_payload)
            assert resp.status_code == 200
            mock_enqueue.assert_not_called()
            mock_send.assert_called_once()

    def test_unsupported_media_type_sends_reply(self, app_client):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "e1",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"display_phone_number": "x", "phone_number_id": "x"},
                                "messages": [
                                    {
                                        "id": "m1",
                                        "from": "591701234567",
                                        "timestamp": "1",
                                        "type": "image",
                                        "image": {"id": "img1"},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
        with (
            patch("app.api.v1.whatsapp.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("app.api.v1.whatsapp.message_buffer_service.enqueue", new_callable=AsyncMock) as mock_enqueue,
        ):
            resp = self._post(app_client, payload)
            assert resp.status_code == 200
            mock_send.assert_called_once()
            mock_enqueue.assert_not_called()

    def test_non_whatsapp_object_returns_ignored(self, app_client):
        payload = {"object": "instagram", "entry": []}
        resp = self._post(app_client, payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_malformed_json_returns_200_not_500(self, app_client):
        """Meta requires 200 even on parse errors to prevent retries."""
        resp = app_client.post(
            "/api/v1/whatsapp/webhook",
            content=b"not valid json {{{{",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

    def test_interactive_button_reply_is_enqueued(self, app_client):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "e1",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"display_phone_number": "x", "phone_number_id": "x"},
                                "messages": [
                                    {
                                        "id": "m1",
                                        "from": "591701234567",
                                        "timestamp": "1",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {"id": "btn1", "title": "Por la mañana"},
                                        },
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
        with patch("app.api.v1.whatsapp.message_buffer_service.enqueue", new_callable=AsyncMock) as mock_enqueue:
            resp = self._post(app_client, payload)
            assert resp.status_code == 200
            mock_enqueue.assert_called_once()
            assert mock_enqueue.call_args[0][1] == "Por la mañana"
