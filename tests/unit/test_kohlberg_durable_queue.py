"""Cola durable de Kohlberg (Redis Streams): cableado hermético (sin red, sin OpenAI).

- El tenant `kohlberg` está registrado (el worker sale si get_tenant es None).
- El flag KOHLBERG_USE_BROKER viene OFF por default (el webhook sigue con el buffer).
- El handler del worker descarta (ACK) un payload inválido sin lanzar ni tocar la red.
"""

import pytest

from app.core.config import settings
from app.core.tenant import get_tenant
from app.worker import _handle_kohlberg_message


def test_kohlberg_tenant_registered():
    """get_tenant('kohlberg') no es None y es del tipo correcto (precondición del worker)."""
    t = get_tenant("kohlberg")
    assert t is not None, "kohlberg debe estar en el env-registry o el worker sale"
    assert t.agent_type == "kohlberg"


def test_use_broker_flag_defaults_off():
    """Por default el flag está OFF: el webhook sigue usando el message_buffer (sin cambio en prod)."""
    assert settings.KOHLBERG_USE_BROKER is False
    assert settings.KOHLBERG_BROKER_TENANT == "kohlberg"


@pytest.mark.asyncio
async def test_kohlberg_handler_drops_invalid_payload():
    """Un payload sin wa_id/text es terminal: retorna (ACK) sin lanzar ni llamar a la red."""
    await _handle_kohlberg_message({})  # no wa_id, no text
    await _handle_kohlberg_message({"wa_id": "591700", "text": ""})  # text vacío
