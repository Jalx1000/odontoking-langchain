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
from functools import partial

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.core.langgraph.odontoking_graph import odontoking_agent
from app.core.limiter import limiter
from app.core.logging import logger
from app.core.tenant import TenantConfig, get_tenant_async
from app.schemas import Message
from app.schemas.whatsapp import WhatsAppWebhookPayload
from app.services.message_buffer import MessageBufferService, ProcessFn, message_buffer_service
from app.services.whatsapp_client import (
    download_media,
    mark_as_read,
    send_response,
    send_text_message,
    send_typing_indicator,
    transcribe_audio,
)

router = APIRouter()

# Background task set — prevents GC of fire-and-forget tasks
_background_tasks: set[asyncio.Task] = set()

# Per-wa_id sliding-window rate limit (in-memory, single process)
_wa_message_times: dict[str, list[float]] = defaultdict(list)
_WA_RATE_WINDOW_SECONDS = 60
_WA_RATE_MAX_MESSAGES = 20

_TIMEOUT_MSG = (
    "Disculpe, la consulta está tardando más de lo esperado. "
    "Por favor intente de nuevo en un momento 🙏."
)
_UNSUPPORTED_MSG = (
    "Disculpe, por el momento solo podemos recibir mensajes de texto y notas de voz 🙏. "
    "Si tiene alguna consulta, escríbanos con texto y con gusto le atendemos 🦷✨."
)

# ── Agent registry ──────────────────────────────────────────────────────────
# Maps tenant_slug → agent instance.
# Phase 3 will replace this with a proper agent factory (BaseAgent pattern).
_AGENT_REGISTRY = {
    "odontoking": odontoking_agent,
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _is_wa_rate_limited(wa_id: str) -> bool:
    now = time.monotonic()
    window = _wa_message_times[wa_id]
    window[:] = [t for t in window if now - t < _WA_RATE_WINDOW_SECONDS]
    if len(window) >= _WA_RATE_MAX_MESSAGES:
        return True
    window.append(now)
    return False


def _make_process_fn(tenant: TenantConfig) -> ProcessFn:
    """Return a ProcessFn closure bound to the given tenant's agent and WA token."""
    agent = _AGENT_REGISTRY.get(tenant.slug, odontoking_agent)

    async def _process(wa_id: str, text: str) -> None:
        messages = [Message(role="user", content=text)]
        try:
            asyncio.create_task(send_typing_indicator(wa_id))
            response_text = await asyncio.wait_for(
                agent.get_response(messages, wa_id=wa_id),
                timeout=settings.LLM_TOTAL_TIMEOUT + 30,
            )
            await send_response(wa_id, response_text)
            logger.info("whatsapp_response_sent", tenant=tenant.slug, wa_id=wa_id, preview=response_text[:60])
        except asyncio.TimeoutError:
            logger.warning("whatsapp_agent_timeout", tenant=tenant.slug, wa_id=wa_id)
            try:
                await send_text_message(wa_id, _TIMEOUT_MSG)
            except Exception:
                pass
        except Exception as e:
            logger.exception("whatsapp_agent_error", tenant=tenant.slug, wa_id=wa_id, error=str(e))
            try:
                await send_text_message(wa_id, "Disculpe, ocurrió un error. Por favor intente de nuevo en un momento 🙏.")
            except Exception:
                pass

    return _process


# Keep a module-level reference so main.py can pass it to buffer.recover()
async def _process_and_reply(wa_id: str, text: str) -> None:
    """Default process fn for the legacy /webhook route (odontoking)."""
    from app.core.tenant import get_tenant as _get
    tenant = _get("odontoking")
    if tenant:
        await _make_process_fn(tenant)(wa_id, text)


# ── Core webhook logic (shared between tenant and legacy routes) ─────────────

async def _handle_webhook_payload(
    raw: bytes,
    tenant: TenantConfig,
    buffer: MessageBufferService,
) -> dict:
    """Parse a Meta webhook payload and enqueue/dispatch messages for the tenant."""
    try:
        data = json.loads(raw)
        payload = WhatsAppWebhookPayload(**data)
    except Exception as e:
        logger.exception("whatsapp_payload_parse_error", tenant=tenant.slug, error=str(e))
        return {"status": "ok"}  # always 200 so Meta does not retry

    if payload.object != "whatsapp_business_account":
        return {"status": "ignored"}

    process_fn = _make_process_fn(tenant)

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
                text_content: str = ""

                if msg_type == "text" and msg.text:
                    text_content = msg.text.body

                elif msg_type == "interactive" and msg.interactive:
                    reply = msg.interactive.button_reply or msg.interactive.list_reply
                    text_content = reply.title if reply else ""

                elif msg_type == "audio" and msg.audio:
                    logger.info("whatsapp_audio_received", tenant=tenant.slug, wa_id=wa_id)
                    try:
                        audio_bytes = await download_media(msg.audio.id)
                        text_content = await transcribe_audio(audio_bytes, msg.audio.mime_type or "audio/ogg")
                        if not text_content:
                            await send_text_message(wa_id, "Disculpe, no pude entender el audio. ¿Podría escribirnos su consulta? 🙏")
                            continue
                    except Exception as e:
                        logger.exception("whatsapp_audio_processing_failed", tenant=tenant.slug, wa_id=wa_id, error=str(e))
                        await send_text_message(wa_id, "Disculpe, tuve un problema procesando su audio. ¿Podría escribirnos? 🙏")
                        continue

                elif msg_type in ("image", "document", "video", "sticker"):
                    try:
                        await send_text_message(wa_id, _UNSUPPORTED_MSG)
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

                task_read = asyncio.create_task(mark_as_read(wa_id, msg.id))
                _background_tasks.add(task_read)
                task_read.add_done_callback(_background_tasks.discard)

                logger.info("whatsapp_message_received", tenant=tenant.slug, wa_id=wa_id, preview=text_content[:60])

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
    """Legacy receive endpoint — routes to odontoking tenant."""
    return await receive_message_tenant(tenant_slug="odontoking", request=request)


# ── Admin / dev endpoints ────────────────────────────────────────────────────

@router.delete("/{tenant_slug}/history/{wa_id}")
async def clear_history(tenant_slug: str, wa_id: str, request: Request) -> dict:
    """Clear conversation history for a WhatsApp number. Dev/admin use only."""
    agent = _AGENT_REGISTRY.get(tenant_slug)
    if agent is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    await agent.clear_history(wa_id)
    logger.info("whatsapp_history_cleared_via_api", tenant=tenant_slug, wa_id=wa_id)
    return {"status": "ok", "tenant": tenant_slug, "wa_id": wa_id}
