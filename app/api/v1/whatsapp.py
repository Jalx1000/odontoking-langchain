"""WhatsApp Cloud API webhook — multi-tenant router (Plan B).

Each Meta Developer App configures its own endpoint:
  GET/POST /api/v1/whatsapp/{tenant_slug}/webhook

Legacy route /api/v1/whatsapp/webhook is kept as a backward-compatible
alias for the "odontoking" tenant while the Meta app config is migrated.
"""

import asyncio
import json
import time
from collections import defaultdict
from hashlib import sha256
from typing import Any

import httpx
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import PlainTextResponse

from app.core.broker import broker
from app.core.config import settings
from app.core.langgraph.odontoking_graph import odontoking_agent
from app.core.langgraph.tools.crm import ensure_lead_registered, ensure_person_registered
from app.core.limiter import limiter
from app.core.logging import logger
from app.core.tenant import TenantConfig, get_tenant, get_tenant_async
from app.schemas import Message
from app.schemas.whatsapp import WhatsAppValue, WhatsAppWebhookPayload
from app.services.gateway import Destination, get_gateway
from app.services.message_buffer import MessageBufferService, ProcessFn, message_buffer_service
from app.services.whatsapp_client import (
    download_media,
    mark_as_read,
    send_text_message,
    transcribe_audio,
)

router = APIRouter()

# Background task set — prevents GC of fire-and-forget tasks
_background_tasks: set[asyncio.Task] = set()

# Per-wa_id sliding-window rate limit (in-memory, single process)
_wa_message_times: dict[str, list[float]] = defaultdict(list)
_WA_RATE_WINDOW_SECONDS = 60
_WA_RATE_MAX_MESSAGES = 20

# Deduplication cache: msg_id → monotonic timestamp
# Meta retries webhook delivery when it doesn't get a 200 fast enough,
# which can cause the same message to be processed twice.
_seen_message_ids: dict[str, float] = {}
_MSG_DEDUPE_TTL = 60.0  # seconds — covers Meta's full retry window

_WAITING_MSG = (
    "La consulta está tardando un poco más de lo normal. "
    "Sigo revisando y le responderé en este mismo chat en un momento 🙏."
)
_HARD_TIMEOUT_MSG = (
    "Disculpe, la consulta no pudo completarse en este momento. "
    "Por favor intente de nuevo en unos minutos 🙏."
)
_UNSUPPORTED_MSG = (
    "Disculpe, por el momento solo podemos recibir mensajes de texto y notas de voz 🙏. "
    "Si tiene alguna consulta, escríbanos con texto y con gusto le atendemos 🦷✨."
)

# ── Agent registry ──────────────────────────────────────────────────────────
# Maps agent_type → internal agent instance.
# External agents (agent_endpoint_url set) bypass this registry entirely.
_AGENT_REGISTRY: dict[str, Any] = {
    "odontoking": odontoking_agent,
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _is_duplicate_message(msg_id: str) -> bool:
    """Return True if this msg_id was already processed within the dedup TTL window."""
    now = time.monotonic()
    expired = [k for k, v in _seen_message_ids.items() if now - v > _MSG_DEDUPE_TTL]
    for k in expired:
        del _seen_message_ids[k]
    if msg_id in _seen_message_ids:
        return True
    _seen_message_ids[msg_id] = now
    return False


def _is_wa_rate_limited(wa_id: str) -> bool:
    now = time.monotonic()
    window = _wa_message_times[wa_id]
    window[:] = [t for t in window if now - t < _WA_RATE_WINDOW_SECONDS]
    if len(window) >= _WA_RATE_MAX_MESSAGES:
        return True
    window.append(now)
    return False


def _extract_profile_name(value: WhatsAppValue) -> str:
    """Return the sender profile name from the webhook payload, if present."""
    contacts = value.contacts or []
    if not contacts:
        return ""
    profile = contacts[0].profile or {}
    if not isinstance(profile, dict):
        return ""
    name = profile.get("name")
    if not isinstance(name, str):
        return ""
    return name.strip()


def _make_process_fn(
    tenant: TenantConfig,
    *,
    is_new_patient: bool = True,
    ci_paciente: str | None = None,
    seguro_paciente: str | None = None,
    nombre_registrado: str | None = None,
    nombre_whatsapp: str | None = None,
) -> ProcessFn:
    """Return a ProcessFn closure bound to the given tenant's agent and WA credentials."""
    agent = _AGENT_REGISTRY.get(tenant.agent_type, odontoking_agent)
    pid = tenant.phone_number_id
    tok = tenant.wa_access_token
    gateway = get_gateway()

    async def _process(wa_id: str, text: str) -> None:
        messages = [Message(role="user", content=text)]
        turn_id = sha256(f"{tenant.slug}:{wa_id}:{time.monotonic_ns()}:{text}".encode()).hexdigest()[:16]
        route = "webhook_direct"
        dest = Destination(wa_id=wa_id, phone_number_id=pid, token=tok)
        agent_task: asyncio.Task | None = None
        try:
            asyncio.create_task(gateway.send_typing(dest))
            logger.info(
                "whatsapp_agent_turn_started",
                tenant=tenant.slug,
                wa_id=wa_id,
                turn_id=turn_id,
                route=route,
                text_preview=text[:120],
            )
            agent_task = asyncio.create_task(
                agent.get_response(
                    messages,
                    wa_id=wa_id,
                    is_new_patient=is_new_patient,
                    ci_paciente=ci_paciente,
                    seguro_paciente=seguro_paciente,
                    nombre_registrado=nombre_registrado,
                    nombre_whatsapp=nombre_whatsapp,
                )
            )
            try:
                response_text = await asyncio.wait_for(
                    asyncio.shield(agent_task),
                    timeout=settings.LLM_TOTAL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning("whatsapp_agent_soft_timeout", tenant=tenant.slug, wa_id=wa_id, turn_id=turn_id)
                await gateway.send_text(dest, _WAITING_MSG)
                response_text = await asyncio.wait_for(
                    asyncio.shield(agent_task),
                    timeout=settings.LLM_TOTAL_TIMEOUT + 30,
                )
            await gateway.send_response(dest, response_text)
            logger.info(
                "whatsapp_response_sent",
                tenant=tenant.slug,
                wa_id=wa_id,
                turn_id=turn_id,
                route=route,
                preview=response_text[:120],
            )
        except asyncio.TimeoutError:
            logger.warning("whatsapp_agent_hard_timeout", tenant=tenant.slug, wa_id=wa_id, turn_id=turn_id)
            if agent_task is not None:
                agent_task.cancel()
            try:
                await gateway.send_text(dest, _HARD_TIMEOUT_MSG)
            except Exception:
                pass
        except Exception as e:
            logger.exception("whatsapp_agent_error", tenant=tenant.slug, wa_id=wa_id, turn_id=turn_id, error=str(e))
            try:
                await gateway.send_text(dest, "Disculpe, ocurrió un error. Por favor intente de nuevo en un momento 🙏.")
            except Exception:
                pass

    return _process


# Keep a module-level reference so main.py can pass it to buffer.recover()
async def _process_and_reply(wa_id: str, text: str) -> None:
    """Default process fn for the legacy /webhook route (odontoking)."""
    tenant = get_tenant("odontoking")
    if tenant:
        await _make_process_fn(tenant)(wa_id, text)


# ── Core webhook logic (shared between tenant and legacy routes) ─────────────

async def _handle_webhook_payload(
    raw: bytes,
    tenant: TenantConfig,
    buffer: MessageBufferService,
) -> dict:
    """Parse a Meta webhook payload and enqueue/dispatch messages for the tenant."""
    # When the active gateway is the CRM middleware, inbound arrives via /api/v1/crm/webhook
    # instead; ignore Meta webhooks so get_gateway() stays consistent with the live path.
    if settings.WHATSAPP_GATEWAY == "sofo-crm":
        logger.info("whatsapp_meta_webhook_inactive_gateway", tenant=tenant.slug)
        return {"status": "ignored"}
    try:
        data = json.loads(raw)
        payload = WhatsAppWebhookPayload(**data)
    except Exception as e:
        logger.exception("whatsapp_payload_parse_error", tenant=tenant.slug, error=str(e))
        return {"status": "ok"}  # always 200 so Meta does not retry

    if payload.object != "whatsapp_business_account":
        return {"status": "ignored"}

    # wa_id → patient context captured from ensure_person_registered
    registered_wa_ids: dict[str, dict] = {}

    for entry in payload.entry:
        for change in entry.changes:
            value = change.value

            if value.statuses and not value.messages:
                continue
            if not value.messages:
                continue

            for msg in value.messages:
                wa_id = msg.from_number
                msg_type = msg.type

                if _is_duplicate_message(msg.id):
                    logger.info("whatsapp_duplicate_message_skipped", tenant=tenant.slug, wa_id=wa_id, msg_id=msg.id)
                    continue

                if wa_id not in registered_wa_ids:
                    try:
                        profile_name = _extract_profile_name(value)
                        # Seed the CRM with the WhatsApp profile name on first contact so the
                        # record is never empty. The intake still asks for the real name and the
                        # CRM name becomes "<perfil WhatsApp> - <nombre solicitado>" afterwards.
                        async with httpx.AsyncClient(timeout=20) as client:
                            reg = await ensure_person_registered(
                                client,
                                wa_id=wa_id,
                                person_name=profile_name,
                                person_phone=wa_id,
                                update_existing_name=False,
                            )
                        registered_wa_ids[wa_id] = {
                            "is_new_patient": reg.get("is_new_patient", True),
                            "ci_paciente": reg.get("ci_paciente"),
                            "seguro_paciente": reg.get("seguro_paciente"),
                            "nombre_registrado": reg.get("nombre_registrado"),
                            "nombre_whatsapp": profile_name or None,
                        }
                        logger.info(
                            "whatsapp_patient_registered",
                            tenant=tenant.slug,
                            wa_id=wa_id,
                            name=profile_name or "Paciente WhatsApp",
                            is_new_patient=registered_wa_ids[wa_id]["is_new_patient"],
                        )
                        # Ensure the patient has a lead in the "Consulta" pipeline stage from the
                        # first message — even a plain greeting, and even for someone who already
                        # exists as a Person but has no lead yet (returning patients, recycled test
                        # numbers). ensure_lead_registered is idempotent (it reuses an existing
                        # lead), so this is safe to attempt on every contact; it runs as a
                        # background task so it never delays the reply.
                        if reg.get("person_id"):
                            lead_task = asyncio.create_task(
                                ensure_lead_registered(wa_id, reg["person_id"], profile_name or None)
                            )
                            _background_tasks.add(lead_task)
                            lead_task.add_done_callback(_background_tasks.discard)
                    except Exception as e:
                        registered_wa_ids[wa_id] = {}
                        logger.exception(
                            "whatsapp_patient_registration_failed",
                            tenant=tenant.slug,
                            wa_id=wa_id,
                            error=str(e),
                        )

                text_content: str = ""

                if msg_type == "text" and msg.text:
                    text_content = msg.text.body

                elif msg_type == "interactive" and msg.interactive:
                    reply = msg.interactive.button_reply or msg.interactive.list_reply
                    text_content = reply.title if reply else ""

                elif msg_type == "audio" and msg.audio:
                    logger.info("whatsapp_audio_received", tenant=tenant.slug, wa_id=wa_id)
                    try:
                        audio_bytes = await download_media(msg.audio.id, token=tenant.wa_access_token)
                        text_content = await transcribe_audio(audio_bytes, msg.audio.mime_type or "audio/ogg")
                        if not text_content:
                            await send_text_message(wa_id, "Disculpe, no pude entender el audio. ¿Podría escribirnos su consulta? 🙏", phone_number_id=tenant.phone_number_id, token=tenant.wa_access_token)
                            continue
                    except Exception as e:
                        logger.exception("whatsapp_audio_processing_failed", tenant=tenant.slug, wa_id=wa_id, error=str(e))
                        await send_text_message(wa_id, "Disculpe, tuve un problema procesando su audio. ¿Podría escribirnos? 🙏", phone_number_id=tenant.phone_number_id, token=tenant.wa_access_token)
                        continue

                elif msg_type in ("image", "document", "video", "sticker"):
                    try:
                        await send_text_message(wa_id, _UNSUPPORTED_MSG, phone_number_id=tenant.phone_number_id, token=tenant.wa_access_token)
                    except Exception:
                        pass
                    continue

                else:
                    logger.info("whatsapp_unsupported_type", tenant=tenant.slug, wa_id=wa_id, msg_type=msg_type)
                    continue

                if not text_content.strip():
                    continue

                if _is_wa_rate_limited(wa_id):
                    logger.warning("whatsapp_rate_limited", tenant=tenant.slug, wa_id=wa_id)
                    continue

                task_read = asyncio.create_task(mark_as_read(wa_id, msg.id, phone_number_id=tenant.phone_number_id, token=tenant.wa_access_token))
                _background_tasks.add(task_read)
                task_read.add_done_callback(_background_tasks.discard)

                logger.info("whatsapp_message_received", tenant=tenant.slug, wa_id=wa_id, preview=text_content[:60])

                # ── Route: external agent (RabbitMQ) vs internal agent (in-process) ─
                if tenant.agent_endpoint_url:
                    patient_ctx = registered_wa_ids.get(wa_id, {})
                    try:
                        await broker.publish(
                            tenant.slug,
                            wa_id,
                            {
                                "text": text_content,
                                "message_id": msg.id,
                                "patient_ctx": patient_ctx,
                            },
                        )
                        logger.info(
                            "whatsapp_message_published_to_broker",
                            tenant=tenant.slug,
                            wa_id=wa_id,
                            message_id=msg.id,
                        )
                    except Exception as e:
                        # Never raise — Meta would retry. Log and drop; DLQ on the
                        # consumer side will surface persistent issues separately.
                        logger.exception(
                            "whatsapp_broker_publish_failed",
                            tenant=tenant.slug,
                            wa_id=wa_id,
                            error=str(e),
                        )
                    continue

                patient_ctx = registered_wa_ids.get(wa_id, {})
                process_fn = _make_process_fn(tenant, **patient_ctx)
                if settings.BUFFER_ENABLED:
                    await buffer.enqueue(wa_id, text_content, process_fn)
                else:
                    task = asyncio.create_task(process_fn(wa_id, text_content))
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)

    return {"status": "ok"}


# ── Tenant routes (Plan B) ───────────────────────────────────────────────────

@router.get("/{tenant_slug}/webhook")
async def verify_webhook_tenant(
    tenant_slug: str,
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> PlainTextResponse:
    """Meta webhook verification for a specific tenant."""
    tenant = await get_tenant_async(tenant_slug)
    if tenant is None:
        logger.warning("whatsapp_unknown_tenant", tenant=tenant_slug)
        raise HTTPException(status_code=404, detail="Tenant not found")

    if hub_mode == "subscribe" and hub_verify_token == tenant.verify_token:
        logger.info("whatsapp_webhook_verified", tenant=tenant_slug)
        return PlainTextResponse(content=hub_challenge)

    logger.warning("whatsapp_webhook_verification_failed", tenant=tenant_slug)
    raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/{tenant_slug}/webhook")
@limiter.limit("100 per minute")
async def receive_message_tenant(tenant_slug: str, request: Request) -> dict:
    """Receive and process incoming WhatsApp messages for a specific tenant."""
    tenant = await get_tenant_async(tenant_slug)
    if tenant is None:
        logger.warning("whatsapp_unknown_tenant_post", tenant=tenant_slug)
        return {"status": "ok"}  # 200 to prevent Meta retries on bad slug

    raw = await request.body()
    logger.info("whatsapp_raw_payload", tenant=tenant_slug, body=raw.decode("utf-8", errors="replace")[:500])
    return await _handle_webhook_payload(raw, tenant, message_buffer_service)


# ── Legacy route — backward-compatible alias for odontoking ─────────────────

@router.get("/webhook")
async def verify_webhook_legacy(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> PlainTextResponse:
    """Legacy verification endpoint — routes to odontoking tenant."""
    return await verify_webhook_tenant(
        tenant_slug="odontoking",
        hub_mode=hub_mode,
        hub_verify_token=hub_verify_token,
        hub_challenge=hub_challenge,
    )


@router.post("/webhook")
@limiter.limit("100 per minute")
async def receive_message_legacy(request: Request) -> dict:
    """Legacy POST webhook — backward-compatible alias for odontoking tenant.

    Meta's webhook is still configured to POST /api/v1/whatsapp/webhook while
    the new tenant-specific route /api/v1/whatsapp/{tenant_slug}/webhook is
    being rolled out. Both routes use the same dedup + buffer pipeline.
    """
    tenant = await get_tenant_async("odontoking")
    if tenant is None:
        logger.warning("whatsapp_legacy_webhook_no_tenant")
        return {"status": "ok"}
    raw = await request.body()
    logger.info("whatsapp_raw_payload", tenant="odontoking", body=raw.decode("utf-8", errors="replace")[:500])
    return await _handle_webhook_payload(raw, tenant, message_buffer_service)



# ── Admin / dev endpoints ────────────────────────────────────────────────────

@router.delete("/{tenant_slug}/history/{wa_id}")
async def clear_history(tenant_slug: str, wa_id: str, request: Request) -> dict:
    """Clear conversation history for a WhatsApp number. Dev/admin use only."""
    _tenant_cfg = get_tenant(tenant_slug)
    agent = _AGENT_REGISTRY.get(_tenant_cfg.agent_type) if _tenant_cfg else None
    if agent is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    await agent.clear_history(wa_id)
    logger.info("whatsapp_history_cleared_via_api", tenant=tenant_slug, wa_id=wa_id)
    return {"status": "ok", "tenant": tenant_slug, "wa_id": wa_id}
