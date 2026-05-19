"""Shared pytest fixtures and configuration."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Force test environment before any app import
os.environ["APP_ENV"] = "test"
os.environ["OPENAI_API_KEY"] = "sk-test-key"
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_DB"] = "test_db"
os.environ["POSTGRES_USER"] = "test_user"
os.environ["POSTGRES_PASSWORD"] = "test_pass"
os.environ["ODONTOKING_API_URL"] = "https://odontoking.test"
os.environ["ODONTOKING_API_TOKEN"] = "test-token"
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = "123456789"
os.environ["WHATSAPP_VERIFY_TOKEN"] = "test-verify-token"
os.environ["WHATSAPP_ACCESS_TOKEN"] = "test-wa-token"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key"
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
os.environ["BUFFER_ENABLED"] = "true"
os.environ["BUFFER_WINDOW_SECONDS"] = "0.05"
os.environ["BUFFER_MAX_MESSAGES"] = "10"
os.environ["ODONTOKING_MEMORY_ENABLED"] = "false"
os.environ["VALKEY_HOST"] = ""  # force in-memory backend


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True)
def reset_wa_seen_ids():
    """Clear the webhook dedup cache before each test to prevent cross-test interference."""
    mod = sys.modules.get("app.api.v1.whatsapp")
    if mod and hasattr(mod, "_seen_message_ids"):
        mod._seen_message_ids.clear()
    yield
    mod = sys.modules.get("app.api.v1.whatsapp")
    if mod and hasattr(mod, "_seen_message_ids"):
        mod._seen_message_ids.clear()


@pytest.fixture
def wa_id() -> str:
    return "591701234567"


@pytest.fixture
def sample_whatsapp_text_payload() -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry-id",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1234", "phone_number_id": "pid"},
                            "contacts": [{"wa_id": "591701234567", "profile": {"name": "Test User"}}],
                            "messages": [
                                {
                                    "id": "msg-id-1",
                                    "from": "591701234567",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "Hola, necesito un turno"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def sample_whatsapp_audio_payload() -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry-id",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1234", "phone_number_id": "pid"},
                            "messages": [
                                {
                                    "id": "msg-id-2",
                                    "from": "591701234567",
                                    "timestamp": "1700000001",
                                    "type": "audio",
                                    "audio": {"id": "audio-media-id", "mime_type": "audio/ogg; codecs=opus"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def sample_whatsapp_status_payload() -> dict:
    """Payload with only a delivery status update — no messages."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry-id",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1234", "phone_number_id": "pid"},
                            "statuses": [{"id": "msg-id-1", "status": "delivered"}],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
