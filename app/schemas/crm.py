"""Pydantic models for the sofo-crm (Krayin) `message.received` webhook event.

See integracion-gateway-whatsapp.md §3. The CRM POSTs this to our agent webhook,
authenticated with `Authorization: Bearer <WHATSAPP_AGENT_TOKEN>`.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class CrmContact(BaseModel):
    """The WhatsApp contact behind the conversation."""

    # phone is OPTIONAL by contract, not by accident: messenger/instagram never carry a phone
    # (Meta identifies the contact by PSID), and kommo may omit it when the CRM's contact lookup
    # fails. Only cloud_api guarantees it. Typing this `str` (required) makes a literal null reject
    # the WHOLE message.received event, so the agent never runs and the message is lost silently.
    phone: Optional[str] = None
    channel: Optional[str] = None  # same value as event.gateway; kept for contract completeness
    name: Optional[str] = None
    person_id: Optional[int] = None
    lead_id: Optional[int] = None

    # Messenger phone-capture signalling. All optional and staged: the CRM rolls this out gradually,
    # so when the fields are ABSENT they must read as "don't ask" (phone_required=False). The CRM owns
    # the counters - we only read them (§4). `phone_required` is a SIGNAL that the phone is missing,
    # not an order to ask now; the agent decides when (once the conversation qualifies).
    phone_required: bool = False
    phone_prompt_state: Optional[str] = None      # pending | asked | captured | refused | null
    phone_prompt_attempts: int = 0                # times the CRM counted us asking (not our counter)
    phone_prompt_exhausted: bool = False          # true = asked 3x already → stop asking


class CrmMessage(BaseModel):
    """The inbound message that triggered the event."""

    id: Optional[int] = None
    type: str = "text"
    text: Optional[str] = None
    timestamp: Optional[str] = None


class CrmHistoryItem(BaseModel):
    """One message of conversation history (role: user | assistant)."""

    model_config = ConfigDict(extra="ignore")

    role: str = "user"
    content: str = ""
    type: str = "text"

    @field_validator("role", "content", "type", mode="before")
    @classmethod
    def _null_to_empty(cls, v: object) -> object:
        """Coerce null history fields to empty strings.

        Kommo/CRM sends `content: null` (and sometimes role/type) for non-text history items
        (images, audio, system notes). Since `content` is typed `str`, a literal null used to
        raise a ValidationError that rejected the WHOLE `message.received` event - so the agent
        never ran and never replied. History is context-only (not the trigger), so a null there
        must never break the event.
        """
        return "" if v is None else v


class CrmWindow(BaseModel):
    """WhatsApp 24h free-text window state."""

    open: bool = True
    expires_at: Optional[str] = None


class CrmReply(BaseModel):
    """Where to POST the reply (§4)."""

    method: str = "POST"
    url: str


class CrmHandoff(BaseModel):
    """Handoff state carried on the event.

    When `open` is true a human advisor is handling the conversation and the agent must NOT respond
    (derivacion-asesor.md §3).
    """

    model_config = ConfigDict(extra="ignore")

    state: Optional[str] = None          # requested | assigned | resolved | null
    open: bool = False
    assigned_user: Optional[int] = None


class CrmWebhookEvent(BaseModel):
    """Root payload for the `message.received` event from the CRM."""

    model_config = ConfigDict(extra="ignore")

    event: str
    conversation_id: int
    # Deliberately a plain str, NOT a closed Enum: new channels get added (instagram is next) and an
    # unknown value must never reject the event. Per-channel behaviour is decided downstream by
    # membership sets (see app/api/v1/crm.py TEXT_ONLY / NO_PHONE), where unknown == text-only,
    # no-phone (the safe default that works everywhere).
    gateway: Optional[str] = None
    ai_enabled: bool = True
    contact: CrmContact
    message: CrmMessage
    history: list[CrmHistoryItem] = []
    window: Optional[CrmWindow] = None
    reply: Optional[CrmReply] = None
    handoff: Optional[CrmHandoff] = None
