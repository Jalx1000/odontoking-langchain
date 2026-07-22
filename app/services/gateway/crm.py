"""sofo-crm gateway — routes replies through the Krayin CRM middleware.

The CRM persists the message and delivers it to the customer via the active gateway
(Kommo salesbot / Cloud API), transparent to us. We authenticate with a Sanctum API
key (settings.CRM_API_KEY) and POST to the conversation's reply endpoint.

The CRM has no typing-indicator or read-receipt API, so those methods are no-ops.
"""

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.services.gateway.base import Destination
from app.services.whatsapp_client import _strip_body_markdown


def _reply_url(dest: Destination) -> str:
    """Resolve the outbound URL: prefer the reply.url from the inbound event."""
    if dest.reply_url:
        return dest.reply_url
    return f"{settings.CRM_BASE_URL}/api/v1/whatsapp/conversations/{dest.conversation_id}/messages"


async def _post_text(dest: Destination, text: str) -> None:
    """POST the reply text to the CRM. Handle the WhatsApp 24h-window 422 gracefully."""
    url = _reply_url(dest)
    payload = {"text": text}
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.CRM_API_KEY}",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        # 422 = outside the 24h WhatsApp window (free text rejected) or a validation error.
        # Never retry free text in that case — log and drop.
        if resp.status_code == 422:
            logger.warning("crm_reply_rejected", wa_id=dest.wa_id, status=422, body=resp.text[:300])
            return
        if not resp.is_success:
            logger.error("crm_reply_error", wa_id=dest.wa_id, status=resp.status_code, body=resp.text[:300])
        resp.raise_for_status()
        logger.info("crm_text_sent", wa_id=dest.wa_id, conversation_id=dest.conversation_id, length=len(text))


class CrmGateway:
    """Sends replies through the Krayin CRM middleware (sofo-crm)."""

    name = "sofo-crm"

    async def send_response(self, dest: Destination, text: str) -> None:
        """Send an agent response to the CRM as plain text.

        The CRM/WhatsApp render Markdown literally, so emphasis markers are stripped like
        the Meta path. Numbered options stay as text (the CRM has no interactive button API).
        """
        await _post_text(dest, _strip_body_markdown(text))

    async def send_text(self, dest: Destination, text: str) -> None:
        """Send a plain text message through the CRM."""
        await _post_text(dest, _strip_body_markdown(text))

    async def send_typing(self, dest: Destination) -> None:
        """No-op: the CRM has no typing-indicator API."""
        logger.debug("crm_typing_noop", wa_id=dest.wa_id)

    async def mark_read(self, dest: Destination, message_id: str) -> None:
        """No-op: the CRM has no read-receipt API."""
        logger.debug("crm_mark_read_noop", wa_id=dest.wa_id, message_id=message_id)
