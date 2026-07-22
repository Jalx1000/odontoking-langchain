"""sofo-crm inbound webhook — receives message.received events from the Krayin CRM.

Active only when WHATSAPP_GATEWAY=sofo-crm. The CRM POSTs here (this URL is the
`WHATSAPP_AGENT_WEBHOOK_URL` configured on the CRM side) authenticated with
`Authorization: Bearer <WHATSAPP_AGENT_TOKEN>`. We reply through the CrmGateway,
which POSTs back to the event's reply.url. See integracion-gateway-whatsapp.md.
"""

import asyncio
import hmac
import time
from hashlib import sha256

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings
from app.core.langgraph.odontoking_graph import odontoking_agent
from app.core.langgraph.tools.crm import ensure_lead_registered, ensure_person_registered, move_lead_to_reception
from app.core.limiter import limiter
from app.core.logging import logger
from app.schemas import Message
from app.schemas.crm import CrmWebhookEvent
from app.services.gateway import Destination, get_gateway
from app.services.message_buffer import message_buffer_service

router = APIRouter()

# Background task set — prevents GC of fire-and-forget tasks
_background_tasks: set[asyncio.Task] = set()

# Deduplication cache: message id → monotonic timestamp (the CRM may retry on slow ACK)
_seen_message_ids: dict[str, float] = {}
_MSG_DEDUPE_TTL = 60.0

_WAITING_MSG = (
    "La consulta está tardando un poco más de lo normal. "
    "Sigo revisando y le responderé en este mismo chat en un momento 🙏."
)
_HARD_TIMEOUT_MSG = (
    "Disculpe, la consulta no pudo completarse en este momento. "
    "Por favor intente de nuevo en unos minutos 🙏."
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _verify_agent_token(request: Request) -> bool:
    """Return True if the request carries the expected CRM agent bearer token."""
    expected = settings.WHATSAPP_AGENT_TOKEN
    if not expected:
        logger.error("crm_webhook_no_agent_token_configured")
        return False
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(token, expected)


def _is_duplicate_message(msg_id: str) -> bool:
    """Return True if this msg_id was already processed within the dedup TTL window."""
    now = time.monotonic()
    for k in [k for k, v in _seen_message_ids.items() if now - v > _MSG_DEDUPE_TTL]:
        del _seen_message_ids[k]
    if msg_id in _seen_message_ids:
        return True
    _seen_message_ids[msg_id] = now
    return False


async def _register_patient(phone: str, name: str | None) -> dict:
    """Find or create the patient in the Odontoking CRM (best-effort). Returns patient ctx."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            reg = await ensure_person_registered(
                client,
                wa_id=phone,
                person_name=name or "",
                person_phone=phone,
                update_existing_name=False,
            )
        ctx = {
            "is_new_patient": reg.get("is_new_patient", True),
            "ci_paciente": reg.get("ci_paciente"),
            "seguro_paciente": reg.get("seguro_paciente"),
            "nombre_registrado": reg.get("nombre_registrado"),
            "nombre_whatsapp": name or None,
        }
        if reg.get("person_id"):
            lead_task = asyncio.create_task(ensure_lead_registered(phone, reg["person_id"], name or None))
            _background_tasks.add(lead_task)
            lead_task.add_done_callback(_background_tasks.discard)
        logger.info("crm_patient_registered", wa_id=phone, is_new_patient=ctx["is_new_patient"])
        return ctx
    except Exception as e:
        logger.exception("crm_patient_registration_failed", wa_id=phone, error=str(e))
        return {}


def _make_process_fn(dest: Destination, patient_ctx: dict, contact_lead_id: int | None = None):
    """Return a ProcessFn closure bound to the CRM destination + patient context."""
    gateway = get_gateway()

    async def _process(wa_id: str, text: str) -> None:
        messages = [Message(role="user", content=text)]
        turn_id = sha256(f"sofo-crm:{wa_id}:{time.monotonic_ns()}:{text}".encode()).hexdigest()[:16]
        agent_task: asyncio.Task | None = None
        # CONTRATO B: the agent fills this when it signals a handoff; we forward it to the CRM
        # after the text reply so the CRM can pause the AI (ai_enabled=false).
        handoff: dict = {}

        async def _on_handoff(signal: dict) -> None:
            handoff.update(signal)

        try:
            logger.info("crm_agent_turn_started", wa_id=wa_id, turn_id=turn_id, text_preview=text[:120])
            agent_task = asyncio.create_task(
                odontoking_agent.get_response(
                    messages,
                    wa_id=wa_id,
                    is_new_patient=patient_ctx.get("is_new_patient", True),
                    ci_paciente=patient_ctx.get("ci_paciente"),
                    seguro_paciente=patient_ctx.get("seguro_paciente"),
                    nombre_registrado=patient_ctx.get("nombre_registrado"),
                    nombre_whatsapp=patient_ctx.get("nombre_whatsapp"),
                    handoff_callback=_on_handoff,
                )
            )
            try:
                response_text = await asyncio.wait_for(
                    asyncio.shield(agent_task), timeout=settings.LLM_TOTAL_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning("crm_agent_soft_timeout", wa_id=wa_id, turn_id=turn_id)
                await gateway.send_text(dest, _WAITING_MSG)
                response_text = await asyncio.wait_for(
                    asyncio.shield(agent_task), timeout=settings.LLM_TOTAL_TIMEOUT + 30
                )
            await gateway.send_response(dest, response_text)
            logger.info("crm_response_sent", wa_id=wa_id, turn_id=turn_id, preview=response_text[:120])
            # CONTRATO B: on a handoff, move the conversation lead to the Recepcionista stage (9).
            # The CRM then stops forwarding this conversation's messages to the agent — the agent
            # does nothing else (no self-silence, no signal endpoint).
            if handoff.get("action") == "handoff":
                result = await move_lead_to_reception(wa_id, lead_id=contact_lead_id)
                logger.info(
                    "crm_handoff_to_reception",
                    wa_id=wa_id,
                    turn_id=turn_id,
                    lead_id=result.get("lead_id"),
                    motivo=handoff.get("motivo", ""),
                    fuera_de_horario=bool(handoff.get("fuera_de_horario", False)),
                    success=result.get("success"),
                )
        except asyncio.TimeoutError:
            logger.warning("crm_agent_hard_timeout", wa_id=wa_id, turn_id=turn_id)
            if agent_task is not None:
                agent_task.cancel()
            try:
                await gateway.send_text(dest, _HARD_TIMEOUT_MSG)
            except Exception:
                pass
        except Exception as e:
            logger.exception("crm_agent_error", wa_id=wa_id, turn_id=turn_id, error=str(e))
            try:
                await gateway.send_text(dest, "Disculpe, ocurrió un error. Por favor intente de nuevo en un momento 🙏.")
            except Exception:
                pass

    return _process


# ── Route ────────────────────────────────────────────────────────────────────

@router.post("/webhook")
@limiter.limit("3000 per minute")
async def receive_crm_event(request: Request) -> dict:
    """Receive and process a message.received event from the sofo-crm middleware."""
    if settings.WHATSAPP_GATEWAY != "sofo-crm":
        logger.info("crm_webhook_inactive_gateway", gateway=settings.WHATSAPP_GATEWAY)
        return {"status": "ignored"}

    if not _verify_agent_token(request):
        logger.warning("crm_webhook_auth_failed")
        raise HTTPException(status_code=401, detail="Unauthorized")

    raw = await request.body()
    logger.info("crm_raw_payload", body=raw.decode("utf-8", errors="replace")[:500])
    try:
        event = CrmWebhookEvent.model_validate_json(raw)
    except Exception as e:
        logger.exception("crm_payload_parse_error", error=str(e))
        return {"status": "ok"}  # 200 so the CRM does not retry a malformed body

    if event.event != "message.received":
        return {"status": "ignored"}

    text = (event.message.text or "").strip()
    if event.message.type != "text" or not text:
        logger.info("crm_unsupported_message", msg_type=event.message.type)
        return {"status": "ignored"}

    msg_key = str(event.message.id or f"{event.conversation_id}:{event.message.timestamp}")
    if _is_duplicate_message(msg_key):
        logger.info("crm_duplicate_message_skipped", conversation_id=event.conversation_id, msg_id=msg_key)
        return {"status": "ok"}

    phone = event.contact.phone.replace("+", "")
    if not phone.isdigit():
        logger.warning("crm_invalid_phone_number", phone=phone)
        return {"status": "ok"}
    reply_url = event.reply.url if event.reply else ""
    dest = Destination(wa_id=phone, conversation_id=event.conversation_id, reply_url=reply_url)

    patient_ctx: dict = {}
    if settings.WHATSAPP_AUTO_CREATE_PERSON:
        patient_ctx = await _register_patient(phone, event.contact.name)

    logger.info("crm_message_received", conversation_id=event.conversation_id, wa_id=phone, preview=text[:60])

    process_fn = _make_process_fn(dest, patient_ctx, contact_lead_id=event.contact.lead_id)
    if settings.BUFFER_ENABLED:
        await message_buffer_service.enqueue(phone, text, process_fn)
    else:
        task = asyncio.create_task(process_fn(phone, text))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return {"status": "ok"}
