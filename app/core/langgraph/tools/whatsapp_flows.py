"""WhatsApp native-forms (flows) tool.

Agendar / cancelar / reprogramar citas are done with native WhatsApp forms published on the CRM
side. The agent only DETECTS the intent and sends the form; the CRM does the rest (cédula,
horarios SMD, crear/cancelar/mover la cita) and confirms to the patient directly in the chat.

Endpoint (sibling of the conversations /messages endpoint the gateway already uses, so it is
addressed through the same CRM base URL and authenticated with the same Sanctum key):

    POST {CRM_BASE_URL}/api/v1/whatsapp/conversations/{conversation_id}/flows
    Authorization: Bearer <CRM_API_KEY>
    {"flow": "paciente_tercero" | "cancelar_cita" | "reprogramar_cita",
     "prefill": {"relation", "patient_name", "relationship", "specialty"}}   # optional

`prefill` is OPTIONAL and only used by the "paciente_tercero" (booking) form to pre‑populate what
the patient already said in chat (who the cita is for + the specialty). The CRM normalizes it and
IGNORES anything it doesn't recognize — missing/invalid values are safe and never break the form,
so it degrades gracefully. Responses: 200 = sent; 422 = conversation with no patient message in
>24h (WhatsApp free-text window closed) → derive to reception. `conversation_id` is injected
server-side (never provided by the LLM).
"""

from typing import Annotated, Optional

import httpx
from langchain_core.tools import InjectedToolArg, tool
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger

# The three published forms. Keep in sync with docs/agente-flows-instrucciones.md (CRM repo).
_VALID_FLOWS = {"paciente_tercero", "cancelar_cita", "reprogramar_cita"}

# Canonical `relation` values for the booking form. The CRM also accepts the aliases it listed
# (para_mi, yo, other, otro) and ignores anything unknown, so this only tidies the obvious cases.
_RELATION_ALIASES = {
    "self": "self", "para_mi": "self", "yo": "self", "mi": "self",
    "tercero": "tercero", "otra_persona": "tercero", "otro": "tercero", "other": "tercero",
}


def _build_prefill(
    relation: Optional[str],
    patient_name: Optional[str],
    relationship: Optional[str],
    specialty: Optional[str],
) -> dict[str, str]:
    """Assemble the booking-form prefill from greeting-derived hints, dropping empty values.

    The CRM ignores values it doesn't recognize (the form still opens), so we only canonicalize the
    obvious `relation` aliases and strip blanks — never guess.
    """
    prefill: dict[str, str] = {}
    if relation and relation.strip():
        key = relation.strip().lower()
        prefill["relation"] = _RELATION_ALIASES.get(key, relation.strip())
    if patient_name and patient_name.strip():
        prefill["patient_name"] = patient_name.strip()
    if relationship and relationship.strip():
        prefill["relationship"] = relationship.strip().lower()
    if specialty and specialty.strip():
        prefill["specialty"] = specialty.strip()
    return prefill


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
async def _post_flow(
    conversation_id: int, flow: str, prefill: Optional[dict[str, str]] = None
) -> httpx.Response:
    """POST the form request to the CRM conversations endpoint. Retries on transient errors."""
    url = f"{settings.CRM_BASE_URL}/api/v1/whatsapp/conversations/{conversation_id}/flows"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.CRM_API_KEY}",
    }
    body: dict[str, object] = {"flow": flow}
    if prefill:
        body["prefill"] = prefill
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=body, headers=headers)
        # 422 is a valid business outcome (window closed), not a transient error → don't retry.
        if resp.status_code != 422:
            resp.raise_for_status()
        return resp


@tool
async def enviar_formulario(
    flow: str,
    relation: Optional[str] = None,
    patient_name: Optional[str] = None,
    relationship: Optional[str] = None,
    specialty: Optional[str] = None,
    conversation_id: Annotated[Optional[int], InjectedToolArg] = None,
) -> str:
    """Envía al paciente un formulario de WhatsApp para agendar, cancelar o reprogramar una cita.

    Usar SIEMPRE en lugar de pedir esos datos por chat. El formulario recoge nombre, cédula,
    especialidad, doctor y horario; el CRM crea/cancela/mueve la cita y confirma al paciente.

    Los parámetros de PRE‑LLENADO (relation/patient_name/relationship/specialty) son OPCIONALES y
    solo aplican a flow="paciente_tercero". Pásalos SOLO con lo que el paciente ya dijo en el chat
    (nunca inventes): el CRM ignora lo que no reconozca y el formulario abre igual.

    Args:
        flow: Uno de "paciente_tercero" (agendar, para el paciente o para otra persona),
            "cancelar_cita" (cancelar) o "reprogramar_cita" (cambiar día u hora).
        relation: Para quién es la cita: "self" (para el propio paciente) o "tercero" (otra persona).
        patient_name: Nombre del paciente de la cita (el titular o el tercero), si el paciente lo dio.
        relationship: Parentesco cuando es un tercero: hijo | padre_madre | pareja | hermano |
            familiar | otro.
        specialty: Especialidad (id o nombre del CRM) o "general"/"No sé". Mapea tú la molestia o
            servicio del paciente a una especialidad; si no puedes mapearla con certeza, omítela.
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

    # Prefill only applies to the booking form; the CRM ignores it for cancelar/reprogramar.
    prefill = (
        _build_prefill(relation, patient_name, relationship, specialty)
        if flow == "paciente_tercero"
        else {}
    )
    log = logger.bind(conversation_id=conversation_id, flow=flow, prefill_keys=sorted(prefill))
    try:
        resp = await _post_flow(conversation_id, flow, prefill or None)
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
