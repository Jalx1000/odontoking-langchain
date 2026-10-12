"""WhatsApp native-forms (flows) tool.

Agendar / cancelar / reprogramar citas are done with native WhatsApp forms published on the CRM
side. The agent only DETECTS the intent and sends the form; the CRM does the rest (cédula,
horarios SMD, crear/cancelar/mover la cita) and confirms to the patient directly in the chat.

Endpoint (sibling of the conversations /messages endpoint the gateway already uses, so it is
addressed through the same CRM base URL and authenticated with the same Sanctum key):

    POST {CRM_BASE_URL}/api/v1/whatsapp/conversations/{conversation_id}/flows
    Authorization: Bearer <CRM_API_KEY>
    {"flow": "paciente_tercero" | "cancelar_cita" | "reprogramar_cita"}

Responses: 200 = sent; 422 = conversation with no patient message in >24h (WhatsApp free-text
window closed) → derive to reception. `conversation_id` is injected server-side (never provided by
the LLM), so the model-facing schema exposes only `flow` — matching the function-calling spec.
"""

from typing import Annotated, Optional

import httpx
from langchain_core.tools import InjectedToolArg, tool
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger

# The three published forms. Keep in sync with docs/agente-flows-instrucciones.md (CRM repo).
_VALID_FLOWS = {"paciente_tercero", "cancelar_cita", "reprogramar_cita"}


def _is_retryable_flow_error(exc: BaseException) -> bool:
    """Retry only transient failures (timeouts, connection errors, 5xx).

    A 422 is definitive (the 24h window is closed) and MUST NOT be retried — it is handled as a
    handoff by the caller, not as a transient error.
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception(_is_retryable_flow_error),
    reraise=True,
)
async def _post_flow(conversation_id: int, flow: str) -> httpx.Response:
    """POST the form request to the CRM conversations endpoint. Retries on transient errors."""
    url = f"{settings.CRM_BASE_URL}/api/v1/whatsapp/conversations/{conversation_id}/flows"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.CRM_API_KEY}",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json={"flow": flow}, headers=headers)
        # 422 is a valid business outcome (window closed), not a transient error → don't retry.
        if resp.status_code != 422:
            resp.raise_for_status()
        return resp


@tool
async def enviar_formulario(
    flow: str,
    conversation_id: Annotated[Optional[int], InjectedToolArg] = None,
) -> str:
    """Envía al paciente un formulario de WhatsApp para agendar, cancelar o reprogramar una cita.

    Usar SIEMPRE en lugar de pedir esos datos por chat. El formulario recoge nombre, cédula,
    especialidad, doctor y horario; el CRM crea/cancela/mueve la cita y confirma al paciente.

    Args:
        flow: Uno de "paciente_tercero" (agendar, para el paciente o para otra persona),
            "cancelar_cita" (cancelar) o "reprogramar_cita" (cambiar día u hora).
        conversation_id: Lo inyecta el sistema desde el evento entrante; tú no lo proporcionas.
    """
    if flow not in _VALID_FLOWS:
        logger.warning("enviar_formulario_invalid_flow", flow=str(flow)[:40])
        return '{"success": false, "error": "invalid_flow"}'

    # conversation_id is injected from the inbound event (not from the LLM). Without it we cannot
    # reach the conversation → tell the agent to derive to reception instead of guessing.
    if not conversation_id:
        logger.warning("enviar_formulario_missing_conversation_id", flow=flow)
        return '{"success": false, "error": "missing_conversation_id", "handoff": true}'

    log = logger.bind(conversation_id=conversation_id, flow=flow)
    try:
        resp = await _post_flow(conversation_id, flow)
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        log.warning("enviar_formulario_transient", error=type(e).__name__)
        return '{"success": false, "error": "service_unavailable", "handoff": true}'
    except httpx.HTTPStatusError as e:
        log.exception("enviar_formulario_http_error", status=e.response.status_code)
        return '{"success": false, "error": "http_error", "handoff": true}'
    except Exception as e:
        log.exception("enviar_formulario_failed", error=str(e))
        return '{"success": false, "error": "unexpected", "handoff": true}'

    if resp.status_code == 422:
        # No patient message in the last 24h → WhatsApp free-text window closed. The form cannot
        # be delivered; derive to reception (per the integration contract).
        log.info("enviar_formulario_window_closed")
        return '{"success": false, "error": "window_closed", "handoff": true}'

    log.info("enviar_formulario_sent", status=resp.status_code)
    return f'{{"success": true, "flow": "{flow}"}}'
