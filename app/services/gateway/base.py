"""Outbound message gateway abstraction.

Defines a provider-agnostic interface for sending WhatsApp replies. Two concrete
implementations exist:
  - MetaGateway  — talks directly to Meta Cloud API (existing behaviour).
  - CrmGateway   — routes replies through the Krayin CRM middleware (sofo-crm),
                   which in turn delivers via Kommo salesbot / Cloud API.

Selection is driven by settings.WHATSAPP_GATEWAY (see app/services/gateway/__init__.py).
Only *sending* is abstracted here; inbound media download/transcription stays in
app/services/whatsapp_client.py because it is Meta-specific.
"""

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass
class Destination:
    """Where to send an outbound message — carries what each provider needs.

    Meta uses wa_id + phone_number_id + token. The CRM uses conversation_id /
    reply_url (both arrive in the inbound message.received event). wa_id is always
    set (the contact phone) and is used for logging on both paths.
    """

    wa_id: str
    phone_number_id: str = ""
    token: str = ""
    conversation_id: Optional[int] = None
    reply_url: str = ""


@runtime_checkable
class MessageGateway(Protocol):
    """Provider-agnostic outbound gateway."""

    name: str

    async def send_response(self, dest: Destination, text: str) -> None:
        """Send an agent response (interactive if the provider supports it, else text)."""
        ...

    async def send_text(self, dest: Destination, text: str) -> None:
        """Send a plain text message."""
        ...

    async def send_typing(self, dest: Destination) -> None:
        """Show a typing indicator. No-op for providers that lack it."""
        ...

    async def send_handoff(self, dest: Destination, motivo: str, fuera_de_horario: bool) -> None:
        """Emit a handoff signal so the provider hands the conversation to a human (CONTRATO B)."""
        ...

    async def mark_read(self, dest: Destination, message_id: str) -> None:
        """Mark an incoming message as read. No-op for providers that lack it."""
        ...
