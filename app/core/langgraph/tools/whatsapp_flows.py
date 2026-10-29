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

import json
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


# Canonical time-of-day buckets the CRM form understands (R: time_slot). Without an exact `time`,
# the form offers one free slot in the chosen franja. Keys below map common phrasings → canonical.
_TIME_SLOTS = {"mañana", "mediodia", "tarde"}
_TIME_SLOT_ALIASES = {
    "manana": "mañana", "mañana": "mañana", "en la mañana": "mañana", "por la mañana": "mañana",
    "temprano": "mañana", "am": "mañana",
    "mediodia": "mediodia", "mediodía": "mediodia", "al mediodia": "mediodia", "al mediodía": "mediodia",
    "medio dia": "mediodia",
    "tarde": "tarde", "en la tarde": "tarde", "por la tarde": "tarde", "pm": "tarde",
}


def _canonical_time_slot(value: Optional[str]) -> Optional[str]:
    """Map a free-text franja to a canonical CRM time_slot (mañana/mediodia/tarde), or None."""
    if not value or not value.strip():
        return None
    key = value.strip().lower()
    if key in _TIME_SLOTS:
        return key
    return _TIME_SLOT_ALIASES.get(key)


def _build_prefill(
    relation: Optional[str],
    patient_name: Optional[str],
    relationship: Optional[str],
    specialty: Optional[str],
    doctor: Optional[str],
    date: Optional[str],
    time: Optional[str],
    time_slot: Optional[str] = None,
) -> dict[str, object]:
    """Assemble the booking-form prefill from greeting-derived hints, dropping empty values.

    The CRM ignores values it doesn't recognize (the form still opens), so we only canonicalize the
    obvious `relation` aliases and strip blanks — never guess. `doctor`/`date`/`time` are PREFERENCES:
    the form confirms them against the doctor's real availability (see R9 in the CRM requirement doc).
    `doctor` is sent as a STRING — the CRM accepts the id as a string ("21") or the doctor name; a
    JSON number is not part of that contract, so an id like "21" must stay "21", not 21.
    """
    prefill: dict[str, object] = {}
    if relation and relation.strip():
        key = relation.strip().lower()
        prefill["relation"] = _RELATION_ALIASES.get(key, relation.strip())
    if patient_name and patient_name.strip():
        prefill["patient_name"] = patient_name.strip()
    if relationship and relationship.strip():
        prefill["relationship"] = relationship.strip().lower()
    if specialty and specialty.strip():
        prefill["specialty"] = specialty.strip()
    if doctor and doctor.strip():
        prefill["doctor"] = doctor.strip()
    if date and date.strip():
        prefill["date"] = date.strip()
    if time and time.strip():
        prefill["time"] = time.strip()
    # A franja (time_slot) is sent ONLY when there is no exact time: with `time` the CRM already
    # suggests the closest slot to that hour, so the bucket would be redundant/conflicting.
    elif (slot := _canonical_time_slot(time_slot)) is not None:
        prefill["time_slot"] = slot
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
    conversation_id: int, flow: str, prefill: Optional[dict[str, object]] = None
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
    doctor: Optional[str] = None,
    date: Optional[str] = None,
    time: Optional[str] = None,
    time_slot: Optional[str] = None,
    conversation_id: Annotated[Optional[int], InjectedToolArg] = None,
    sin_cita_activa: Annotated[bool, InjectedToolArg] = False,
) -> str:
    """Envía al paciente un formulario de WhatsApp para agendar, cancelar o reprogramar una cita.

    Usar SIEMPRE en lugar de pedir esos datos por chat. El formulario recoge nombre, cédula,
    especialidad, doctor y horario; el CRM crea/cancela/mueve la cita y confirma al paciente.

    Los parámetros de PRE‑LLENADO son OPCIONALES y solo aplican a flow="paciente_tercero". Pásalos
    SOLO con lo que el paciente ya dijo en el chat (nunca inventes): el CRM ignora lo que no reconozca
    y el formulario abre igual. doctor/date/time son PREFERENCIAS: el formulario confirma el cupo
    según la disponibilidad real del doctor.

    Args:
        flow: Uno de "paciente_tercero" (agendar, para el paciente o para otra persona),
            "cancelar_cita" (cancelar) o "reprogramar_cita" (cambiar día u hora).
        relation: Para quién es la cita: "self" (para el propio paciente) o "tercero" (otra persona).
        patient_name: Nombre del paciente de la cita (el titular o el tercero), si el paciente lo dio.
        relationship: Parentesco cuando es un tercero: hijo | padre_madre | pareja | hermano |
            familiar | otro.
        specialty: Especialidad (id o nombre del CRM) o "general"/"No sé". Mapea tú la molestia o
            servicio del paciente a una especialidad; si no puedes mapearla con certeza, omítela.
        doctor: Doctor pedido ("con el Dr. Eloy"). PREFERIBLE su ID del CRM (resuélvelo con get_doctors,
            p. ej. "21"); también acepta el nombre si coincide con un solo doctor. Si mandas doctor,
            specialty es opcional (el form toma la del doctor). Omítelo si no lo identificas con certeza.
        date: Día pedido en formato AAAA-MM-DD (convierte "el viernes"/"mañana" con la fecha actual).
        time: Hora EXACTA pedida en formato HH:MM (24h), p. ej. "13:00". Solo si el paciente dio una
            hora concreta; una franja ("en la tarde") NO es hora exacta → usa time_slot en su lugar.
        time_slot: Franja del día cuando el paciente NO dio hora exacta: "mañana", "mediodia" o
            "tarde" (ej. "en la tarde" → "tarde"). El formulario ofrece un cupo libre de esa franja.
            Si el paciente no dio ni hora ni franja, omite ambos (el form sugiere el cupo más próximo).
        conversation_id: Lo inyecta el sistema desde el evento entrante; tú no lo proporcionas.
        sin_cita_activa: Lo inyecta el sistema (no lo proporcionas); marca que el paciente no tiene
            cita activa para bloquear cancelar/reprogramar.
    """
    if flow not in _VALID_FLOWS:
        logger.warning("enviar_formulario_invalid_flow", flow=str(flow)[:40])
        return '{"success": false, "error": "invalid_flow"}'

    # Guard (injected by the graph): cancelar/reprogramar without an active cita must NOT send the
    # form — the patient has nothing to cancel/move. Signal the agent to offer booking a new one.
    if sin_cita_activa and flow in ("cancelar_cita", "reprogramar_cita"):
        logger.info("enviar_formulario_sin_cita_activa", flow=flow)
        return ('{"success": false, "error": "sin_cita_activa", '
                '"instruccion": "El paciente no tiene una cita activa. Avísale que no encontraste una '
                'cita a su nombre y envía el formulario de agendar (flow=paciente_tercero) por si desea '
                'una nueva."}')

    # conversation_id is injected from the inbound event (not from the LLM). Without it we cannot
    # reach the conversation → tell the agent to derive to reception instead of guessing.
    if not conversation_id:
        logger.warning("enviar_formulario_missing_conversation_id", flow=flow)
        return '{"success": false, "error": "missing_conversation_id", "handoff": true}'

    # Prefill only applies to the booking form; the CRM ignores it for cancelar/reprogramar.
    prefill = (
        _build_prefill(relation, patient_name, relationship, specialty, doctor, date, time, time_slot)
        if flow == "paciente_tercero"
        else {}
    )
    log = logger.bind(conversation_id=conversation_id, flow=flow, prefill=prefill or None)
    # The welcome greeting on first contact rides in the agent's reply text (prepended deterministically
    # in get_response) and the CRM delivers that text before the form — so the tool doesn't send it.
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


_VALID_RESPUESTAS = {"confirma", "no_asiste"}


@tool
async def responder_recordatorio(
    respuesta: str,
    activity_id: Optional[int] = None,
    lead_id: Optional[int] = None,
    conversation_id: Annotated[Optional[int], InjectedToolArg] = None,
) -> str:
    """Registra la respuesta del paciente a un recordatorio de cita.

    Úsala SOLO cuando el paciente responde a un recordatorio (mira `recordatorios` en el contexto:
    un recordatorio con respuesta null está sin contestar):
    - "confirma": el paciente confirma que asistirá → la cita queda en "Cita confirmada".
    - "no_asiste": el paciente avisa que NO podrá ir → NO cancela nada, solo deja aviso a recepción;
      DESPUÉS ofrécele reprogramar o cancelar con el formulario correspondiente.

    Pasa `activity_id` (o `lead_id`) del recordatorio sin contestar. Si lo omites y hay un solo
    recordatorio pendiente, se aplica a ese; si hay varios, el CRM responde multiple_reminders y debes
    llamar una vez por cada cita con su activity_id.

    Args:
        respuesta: "confirma" o "no_asiste".
        activity_id: id de la actividad/cita del recordatorio (de `recordatorios` en el contexto).
        lead_id: alternativa al activity_id.
        conversation_id: lo inyecta el sistema desde el evento entrante; tú no lo proporcionas.
    """
    if respuesta not in _VALID_RESPUESTAS:
        logger.warning("responder_recordatorio_invalid", respuesta=str(respuesta)[:40])
        return '{"success": false, "error": "invalid_respuesta"}'
    if not conversation_id:
        logger.warning("responder_recordatorio_missing_conversation_id")
        return '{"success": false, "error": "missing_conversation_id", "handoff": true}'

    body: dict[str, object] = {"respuesta": respuesta}
    if activity_id:
        body["activity_id"] = activity_id
    if lead_id:
        body["lead_id"] = lead_id
    url = f"{settings.CRM_BASE_URL}/api/v1/whatsapp/conversations/{conversation_id}/appointments/respond"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.CRM_API_KEY}",
    }
    log = logger.bind(
        conversation_id=conversation_id, respuesta=respuesta, activity_id=activity_id, lead_id=lead_id
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=body, headers=headers)
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        log.warning("responder_recordatorio_transient", error=type(e).__name__)
        return '{"success": false, "error": "service_unavailable", "handoff": true}'
    except Exception as e:
        log.exception("responder_recordatorio_failed", error=str(e))
        return '{"success": false, "error": "unexpected", "handoff": true}'

    # 422 = more than one unanswered reminder: the CRM returns the list so the agent calls once per
    # cita with its activity_id. Not a transient error.
    if resp.status_code == 422:
        log.info("responder_recordatorio_ambiguous")
        return json.dumps(
            {"success": False, "error": "multiple_reminders", "detail": resp.text[:500]},
            ensure_ascii=False,
        )
    if resp.status_code >= 400:
        log.warning("responder_recordatorio_http_error", status=resp.status_code)
        return '{"success": false, "error": "http_error", "handoff": true}'

    log.info("responder_recordatorio_sent", status=resp.status_code)
    return json.dumps({"success": True, "respuesta": respuesta}, ensure_ascii=False)
