"""WhatsApp Cloud API client — send messages and transcribe audio."""

import io
import re
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import logger

_GRAPH_URL = "https://graph.facebook.com/v19.0"


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}


async def send_text_message(to: str, text: str) -> dict:
    """Send a plain text message via WhatsApp Cloud API."""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_GRAPH_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
            json=payload,
            headers={**_auth_headers(), "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        logger.info("whatsapp_text_sent", to=to, length=len(text))
        return resp.json()


async def send_interactive_message(to: str, interactive: dict) -> dict:
    """Send an interactive (button or list) message via WhatsApp Cloud API."""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_GRAPH_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
            json=payload,
            headers={**_auth_headers(), "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        logger.info("whatsapp_interactive_sent", to=to, type=interactive.get("type"))
        return resp.json()


def build_interactive_payload(mensaje: str, to: str) -> Optional[dict]:
    """Detect numbered options in mensaje and return an interactive payload dict.

    Returns None when the message has no numbered options (plain text).
    """
    option_pattern = re.compile(r"\d+\)\s*([^\n]+)")
    options = option_pattern.findall(mensaje)

    if len(options) < 2:
        return None

    body_text = option_pattern.split(mensaje)[0].strip() or mensaje[:1024]

    if len(options) == 2:
        return {
            "type": "button",
            "body": {"text": body_text[:1024]},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": opt[:256], "title": opt[:20]}}
                    for opt in options[:3]
                ]
            },
        }

    return {
        "type": "list",
        "header": {"type": "text", "text": "Seleccione una opción"},
        "body": {"text": body_text[:1024]},
        "action": {
            "button": "Ver opciones",
            "sections": [
                {
                    "title": "Opciones disponibles",
                    "rows": [
                        {
                            "id": f"opt_{i + 1}",
                            "title": opt[:24],
                            "description": "",
                        }
                        for i, opt in enumerate(options[:10])
                    ],
                }
            ],
        },
    }


async def send_response(to: str, mensaje: str) -> None:
    """Send a WhatsApp response — interactive if options detected, plain text otherwise."""
    interactive = build_interactive_payload(mensaje, to)
    try:
        if interactive:
            await send_interactive_message(to, interactive)
        else:
            await send_text_message(to, mensaje)
    except httpx.HTTPStatusError as e:
        logger.exception("whatsapp_send_failed", to=to, status=e.response.status_code, error=str(e))
        raise


async def download_media(media_id: str) -> bytes:
    """Download media bytes from WhatsApp Cloud API using media_id."""
    async with httpx.AsyncClient(timeout=30) as client:
        meta_resp = await client.get(
            f"{_GRAPH_URL}/{media_id}",
            headers=_auth_headers(),
        )
        meta_resp.raise_for_status()
        media_url = meta_resp.json().get("url", "")

        file_resp = await client.get(media_url, headers=_auth_headers())
        file_resp.raise_for_status()
        logger.info("whatsapp_media_downloaded", media_id=media_id, bytes=len(file_resp.content))
        return file_resp.content


async def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Transcribe audio bytes using OpenAI Whisper API.

    Returns the transcription text, or empty string on failure.
    """
    ext_map = {
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp4": "mp4",
        "audio/webm": "webm",
        "audio/wav": "wav",
    }
    ext = ext_map.get(mime_type, "ogg")
    filename = f"audio.{ext}"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                files={"file": (filename, io.BytesIO(audio_bytes), mime_type)},
                data={"model": "whisper-1", "language": "es"},
            )
            resp.raise_for_status()
            text = resp.json().get("text", "")
            logger.info("audio_transcribed", chars=len(text))
            return text
    except Exception as e:
        logger.exception("audio_transcription_failed", error=str(e))
        return ""
