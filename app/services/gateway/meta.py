"""Meta Cloud API gateway — thin adapter over app/services/whatsapp_client.py."""

from app.services.gateway.base import Destination
from app.services.whatsapp_client import (
    mark_as_read,
    send_response,
    send_text_message,
    send_typing_indicator,
)


class MetaGateway:
    """Sends WhatsApp replies directly via Meta Cloud API.

    Delegates to the existing whatsapp_client functions, preserving all current
    behaviour (interactive button/list payloads, Markdown stripping, etc.).
    """

    name = "meta"

    async def send_response(self, dest: Destination, text: str) -> None:
        """Send an agent response (interactive if options detected, plain text otherwise)."""
        await send_response(dest.wa_id, text, phone_number_id=dest.phone_number_id, token=dest.token)

    async def send_text(self, dest: Destination, text: str) -> None:
        """Send a plain text message via Meta Cloud API."""
        await send_text_message(dest.wa_id, text, phone_number_id=dest.phone_number_id, token=dest.token)

    async def send_typing(self, dest: Destination) -> None:
        """Show the WhatsApp typing indicator (best-effort)."""
        await send_typing_indicator(dest.wa_id, phone_number_id=dest.phone_number_id, token=dest.token)

    async def mark_read(self, dest: Destination, message_id: str) -> None:
        """Mark an incoming message as read (blue ticks)."""
        await mark_as_read(dest.wa_id, message_id, phone_number_id=dest.phone_number_id, token=dest.token)
