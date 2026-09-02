"""sofo-crm inbound webhook - receives message.received events from the Krayin CRM.

Active only when WHATSAPP_GATEWAY=sofo-crm. The CRM POSTs here (this URL is the
`WHATSAPP_AGENT_WEBHOOK_URL` configured on the CRM side) authenticated with
`Authorization: Bearer <WHATSAPP_AGENT_TOKEN>`. We reply through the CrmGateway,
which POSTs back to the event's reply.url. See integracion-gateway-whatsapp.md.
"""

import asyncio
import hmac
import time
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings
from app.core.langgraph.kohlberg_graph import kohlberg_agent
from app.core.langgraph.tools.kohlberg import request_handoff
from app.core.limiter import limiter
from app.core.logging import logger
from app.schemas import Message
from app.schemas.crm import CrmWebhookEvent
from app.services.gateway import Destination, get_gateway
from app.services.message_buffer import message_buffer_service

router = APIRouter()

# Background task set - prevents GC of fire-and-forget tasks
_background_tasks: set[asyncio.Task] = set()

# Deduplication cache: message id → monotonic timestamp (the CRM may retry on slow ACK)
_seen_message_ids: dict[str, float] = {}
_MSG_DEDUPE_TTL = 60.0

# Per-channel capabilities. Membership, not an allowlist: a channel NOT listed here is treated as
# the safe default (text-only, no phone), so a new gateway never needs a code change to be handled.
# The CRM gateway only ever sends text anyway (see services/gateway/crm.py), so media degradation is
# structural; these sets exist to document the contract and gate phone-dependent logic.
_TEXT_ONLY = {"kommo"}       # channels that only relay text
_NO_PHONE = {"messenger"}    # channels that never carry a phone (Meta PSID identity)

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


def _make_process_fn(dest: Destination, patient_ctx: dict):
    """Return a ProcessFn closure bound to the CRM destination + contact context."""
    gateway = get_gateway()

    async def _process(wa_id: str, text: str) -> None:
        messages = [Message(role="user", content=text)]
        turn_id = sha256(f"sofo-crm:{wa_id}:{time.monotonic_ns()}:{text}".encode()).hexdigest()[:16]
        agent_task: asyncio.Task | None = None
        # Filled by the agent (via handoff_callback) when it calls derivar_a_asesor this turn.
        handoff: dict = {}

        async def _on_handoff(signal: dict) -> None:
            handoff.update(signal)

        try:
            logger.info("crm_agent_turn_started", wa_id=wa_id, turn_id=turn_id, text_preview=text[:120])
            agent_task = asyncio.create_task(
                kohlberg_agent.get_response(
                    messages,
                    wa_id,
                    conversation_id=dest.conversation_id,
                    lead_id=patient_ctx.get("lead_id"),
                    person_id=patient_ctx.get("person_id"),
                    channel=patient_ctx.get("channel"),
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
            # Derive AFTER the reply (the reply is the client's notice): once derived the CRM 409s any
            # further /messages, so order matters. The CRM routes to the city's advisor (or the pool).
            if "reason" in handoff and dest.conversation_id is not None:
                await request_handoff(dest.conversation_id, handoff.get("reason", ""), handoff.get("ciudad"))
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
        # 422, NOT 200. Returning 200 on a parse failure hid this bug for weeks: the CRM saw success,
        # never logged an error, and dropped every message in silence. 422 makes the CRM record it.
        # Retrying won't help (a deterministic body fails identically all 4 times) - but visibility is
        # the point; a transient fault would surface as 5xx below, where the CRM's retries do rescue it.
        logger.exception("crm_payload_parse_error", error=str(e))
        raise HTTPException(status_code=422, detail="unparseable message.received payload") from e

    if event.event != "message.received":
        return {"status": "ignored"}

    # A human advisor is already handling this conversation - stay silent (derivacion-asesor.md §3).
    if event.handoff and event.handoff.open:
        logger.info(
            "crm_conversation_handed_off_skip",
            conversation_id=event.conversation_id,
            state=event.handoff.state,
        )
        return {"status": "ignored"}

    # Accept text AND interactive (button/list replies): the CRM now puts the selected option's label
    # in `message.text` for interactive messages, so a city/option picker resolves to plain text. Any
    # other type (image/audio/document/location) still carries no usable text and is ignored.
    text = (event.message.text or "").strip()
    if event.message.type not in ("text", "interactive") or not text:
        logger.info("crm_unsupported_message", msg_type=event.message.type)
        return {"status": "ignored"}

    msg_key = str(event.message.id or f"{event.conversation_id}:{event.message.timestamp}")
    if _is_duplicate_message(msg_key):
        logger.info("crm_duplicate_message_skipped", conversation_id=event.conversation_id, msg_id=msg_key)
        return {"status": "ok"}

    # Contact identity. The phone can be absent (messenger never has one; kommo may omit it) - never
    # drop the message for that. Fall back to a stable per-conversation key so the checkpointer thread
    # and mem0 stay keyed consistently across the conversation's turns.
    phone = (event.contact.phone or "").replace("+", "")
    if phone and not phone.isdigit():
        logger.warning("crm_invalid_phone_number", phone=phone, conversation_id=event.conversation_id)
        phone = ""
    convo_key = phone or f"conv:{event.conversation_id}"
    reply_url = event.reply.url if event.reply else ""
    dest = Destination(wa_id=convo_key, conversation_id=event.conversation_id, reply_url=reply_url)

    # lead_id / person_id come straight from the CRM event: the CRM auto-opens ONE lead per
    # conversation (contact.lead_id) and the agent moves/enriches it - it does not create its own.
    patient_ctx: dict = {
        "nombre_whatsapp": event.contact.name or None,
        "channel": event.contact.channel or event.gateway,
        # The CRM auto-opens ONE lead per conversation (contact.lead_id / contact.person_id). We forward
        # them so registrar_pedido enriches that lead instead of creating a duplicate.
        "lead_id": event.contact.lead_id,
        "person_id": event.contact.person_id,
    }

    logger.info(
        "crm_message_received",
        conversation_id=event.conversation_id,
        wa_id=convo_key,
        channel=event.gateway,
        preview=text[:60],
    )
    # Diagnostic: shows whether the CRM is actually sending the phone-capture signal on this channel.
    # If phone_required stays false/absent on messenger, the agent will (correctly) never ask.
    logger.info(
        "crm_phone_prompt",
        conversation_id=event.conversation_id,
        channel=event.contact.channel or event.gateway,
        phone_present=bool(event.contact.phone),
        phone_required=event.contact.phone_required,
        phone_prompt_state=event.contact.phone_prompt_state,
        phone_prompt_attempts=event.contact.phone_prompt_attempts,
        phone_prompt_exhausted=event.contact.phone_prompt_exhausted,
    )

    process_fn = _make_process_fn(dest, patient_ctx)
    # A failure to enqueue/schedule here is transient (buffer/loop hiccup), not a bad payload: answer
    # 503 so the CRM's retries (1 + 3 spaced a minute) actually rescue the message.
    try:
        if settings.BUFFER_ENABLED:
            await message_buffer_service.enqueue(convo_key, text, process_fn)
        else:
            task = asyncio.create_task(process_fn(convo_key, text))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
    except Exception as e:
        logger.exception("crm_enqueue_failed", conversation_id=event.conversation_id, error=str(e))
        raise HTTPException(status_code=503, detail="agent temporarily unavailable") from e

    return {"status": "ok"}
