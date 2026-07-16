"""Pydantic models for the sofo-crm (Krayin) `message.received` webhook event.

See integracion-gateway-whatsapp.md §3. The CRM POSTs this to our agent webhook,
authenticated with `Authorization: Bearer <WHATSAPP_AGENT_TOKEN>`.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class CrmContact(BaseModel):
    """The WhatsApp contact behind the conversation."""

    phone: str
    name: Optional[str] = None
    person_id: Optional[int] = None
    lead_id: Optional[int] = None


class CrmMessage(BaseModel):
    """The inbound message that triggered the event."""

    id: Optional[int] = None
    type: str = "text"
    text: Optional[str] = None
    timestamp: Optional[str] = None


class CrmHistoryItem(BaseModel):
    """One message of conversation history (role: user | assistant)."""

    model_config = ConfigDict(extra="ignore")

    role: str
    content: str = ""
    type: str = "text"


class CrmWindow(BaseModel):
    """WhatsApp 24h free-text window state."""

    open: bool = True
    expires_at: Optional[str] = None


class CrmReply(BaseModel):
    """Where to POST the reply (§4)."""

    method: str = "POST"
    url: str


class CrmWebhookEvent(BaseModel):
    """Root payload for the `message.received` event from the CRM."""

    model_config = ConfigDict(extra="ignore")

    event: str
    conversation_id: int
    gateway: Optional[str] = None
    ai_enabled: bool = True
    contact: CrmContact
    message: CrmMessage
    history: list[CrmHistoryItem] = []
    window: Optional[CrmWindow] = None
    reply: Optional[CrmReply] = None
