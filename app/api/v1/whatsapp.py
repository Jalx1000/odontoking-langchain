"""WhatsApp Cloud API webhook — receive messages and respond via OdontokingAgent."""

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
from app.schemas import Message
from app.schemas.whatsapp import WhatsAppWebhookPayload
from app.services.message_buffer import message_buffer_service
from app.services.whatsapp_client import (
    download_media,
    send_response,
    send_text_message,
    transcribe_audio,
)

router = APIRouter()


async def _process_and_reply(wa_id: str, text: str) -> None:
    """Invoke the agent and send the response back to the user."""
    messages = [Message(role="user", content=text)]
    try:
        response_text = await odontoking_agent.get_response(messages, wa_id=wa_id)
        await send_response(wa_id, response_text)
        logger.info("whatsapp_response_sent", wa_id=wa_id, preview=response_text[:60])
    except Exception as e:
        logger.exception("whatsapp_agent_error", wa_id=wa_id, error=str(e))
        try:
            await send_text_message(wa_id, "Disculpe, ocurrió un error. Por favor intente de nuevo en un momento 🙏.")
        except Exception:
            pass


_UNSUPPORTED_MSG = (
    "Disculpe, por el momento solo podemos recibir mensajes de texto y notas de voz 🙏. "
    "Si tiene alguna consulta, escríbanos con texto y con gusto le atendemos 🦷✨."
)


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> PlainTextResponse:
    """Meta webhook verification — returns hub.challenge when token matches."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("whatsapp_webhook_verified")
        return PlainTextResponse(content=hub_challenge)
    logger.warning("whatsapp_webhook_verification_failed", token=hub_verify_token)
    raise HTTPException(status_code=403, detail="Forbidden")


@router.delete("/history/{wa_id}")
async def clear_history(wa_id: str, request: Request) -> dict:
    """Clear conversation history for a WhatsApp number. Dev/admin use only."""
    await odontoking_agent.clear_history(wa_id)
    logger.info("whatsapp_history_cleared_via_api", wa_id=wa_id)
    return {"status": "ok", "wa_id": wa_id}


@router.post("/webhook")
@limiter.limit("100 per minute")
async def receive_message(request: Request) -> dict:
    """Receive and process incoming WhatsApp messages."""
    raw = await request.body()
    logger.info("whatsapp_raw_payload", body=raw.decode("utf-8", errors="replace")[:500])

    import json as _json
    try:
        data = _json.loads(raw)
        payload = WhatsAppWebhookPayload(**data)
    except Exception as e:
        logger.exception("whatsapp_payload_parse_error", error=str(e), raw=raw.decode()[:300])
        return {"status": "ok"}  # siempre 200 para que Meta no reintente
    """Receive and process incoming WhatsApp messages."""
    if payload.object != "whatsapp_business_account":
        return {"status": "ignored"}

    for entry in payload.entry:
        for change in entry.changes:
            value = change.value

            # Skip status updates (delivered, read, etc.)
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
                    logger.info("whatsapp_audio_received", wa_id=wa_id, media_id=msg.audio.id)
                    try:
                        audio_bytes = await download_media(msg.audio.id)
                        text_content = await transcribe_audio(audio_bytes, msg.audio.mime_type or "audio/ogg")
                        if not text_content:
                            await send_text_message(
                                wa_id,
                                "Disculpe, no pude entender el audio. ¿Podría escribirnos su consulta? 🙏",
                            )
                            continue
                        logger.info("whatsapp_audio_transcribed", wa_id=wa_id, text_preview=text_content[:50])
                    except Exception as e:
                        logger.exception("whatsapp_audio_processing_failed", wa_id=wa_id, error=str(e))
                        await send_text_message(
                            wa_id,
                            "Disculpe, tuve un problema procesando su audio. ¿Podría escribirnos? 🙏",
                        )
                        continue

                elif msg_type in ("image", "document", "video", "sticker"):
                    try:
                        await send_text_message(wa_id, _UNSUPPORTED_MSG)
                    except Exception as e:
                        logger.exception("whatsapp_unsupported_reply_failed", wa_id=wa_id, error=str(e))
                    continue

                else:
                    logger.info("whatsapp_unsupported_type", wa_id=wa_id, msg_type=msg_type)
                    continue

                if not text_content.strip():
                    continue

                # Buffer the message; worker will process after BUFFER_WINDOW_SECONDS
                logger.info("whatsapp_message_received", wa_id=wa_id, preview=text_content[:60])
                await message_buffer_service.enqueue(wa_id, text_content, _process_and_reply)

    return {"status": "ok"}
