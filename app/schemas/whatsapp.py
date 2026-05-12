"""Pydantic models for Meta WhatsApp Cloud API webhook payloads."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class WhatsAppTextMessage(BaseModel):
    """Plain text message body."""

    body: str


class WhatsAppAudioMessage(BaseModel):
    """Audio message with media ID and optional MIME type."""

    id: str
    mime_type: Optional[str] = None


class WhatsAppImageMessage(BaseModel):
    """Image message with media ID and optional MIME type."""

    id: str
    mime_type: Optional[str] = None


class WhatsAppDocumentMessage(BaseModel):
    """Document message with media ID and optional MIME type."""

    id: str
    mime_type: Optional[str] = None


class WhatsAppInteractiveReply(BaseModel):
    """Reply from a button or list interactive message."""

    id: str
    title: str


class WhatsAppInteractiveMessage(BaseModel):
    """Interactive message containing a button or list reply selection."""

    type: str
    button_reply: Optional[WhatsAppInteractiveReply] = None
    list_reply: Optional[WhatsAppInteractiveReply] = None


class WhatsAppIncomingMessage(BaseModel):
    """A single incoming message from a WhatsApp user."""

    id: str
    from_number: str = Field(alias="from")
    timestamp: str
    type: str
    text: Optional[WhatsAppTextMessage] = None
    audio: Optional[WhatsAppAudioMessage] = None
    image: Optional[WhatsAppImageMessage] = None
    document: Optional[WhatsAppDocumentMessage] = None
    interactive: Optional[WhatsAppInteractiveMessage] = None

    model_config = {"populate_by_name": True}


class WhatsAppContact(BaseModel):
    """Contact information associated with a WhatsApp message."""

    wa_id: str
    profile: Optional[dict[str, Any]] = None


class WhatsAppValue(BaseModel):
    """Value object containing messaging product, metadata, contacts, messages, and statuses."""

    messaging_product: str
    metadata: dict[str, Any]
    contacts: Optional[list[WhatsAppContact]] = None
    messages: Optional[list[WhatsAppIncomingMessage]] = None
    statuses: Optional[list[Any]] = None


class WhatsAppChange(BaseModel):
    """Change object representing a change event in the webhook payload."""

    value: WhatsAppValue
    field: str


class WhatsAppEntry(BaseModel):
    """Entry object representing an entry in the webhook payload containing changes."""

    id: str
    changes: list[WhatsAppChange]


class WhatsAppWebhookPayload(BaseModel):
    """Root webhook payload from Meta WhatsApp Cloud API containing object type and entries."""

    object: str
    entry: list[WhatsAppEntry]
