"""Unit tests for the WhatsApp worker message handler."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_worker_passes_patient_context_to_agent():
    """Broker/worker path must call the agent with the same patient context as webhook direct."""
    from app.worker import _handle_message

    agent = AsyncMock()
    agent.get_response = AsyncMock(return_value="ok")
    payload = {
        "wa_id": "59170088388",
        "text": "Quiero agendar",
        "message_id": "wamid.test",
        "patient_ctx": {
            "is_new_patient": False,
            "ci_paciente": "5833699",
            "seguro_paciente": "Membresía Odontoking",
            "nombre_registrado": "ABRAM FRIESEN WALL",
            "nombre_whatsapp": "Sofopolis",
        },
    }

    with patch("app.worker.get_gateway", return_value=AsyncMock()):
        await _handle_message(payload, agent, "odontoking")

    agent.get_response.assert_awaited_once()
    kwargs = agent.get_response.await_args.kwargs
    assert kwargs["wa_id"] == "59170088388"
    assert kwargs["is_new_patient"] is False
    assert kwargs["ci_paciente"] == "5833699"
    assert kwargs["seguro_paciente"] == "Membresía Odontoking"
    assert kwargs["nombre_registrado"] == "ABRAM FRIESEN WALL"
    assert kwargs["nombre_whatsapp"] == "Sofopolis"
