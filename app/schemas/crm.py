"""Pydantic models for the sofo-crm (Krayin) `message.received` webhook event.

See integracion-gateway-whatsapp.md §3. The CRM POSTs this to our agent webhook,
authenticated with `Authorization: Bearer <WHATSAPP_AGENT_TOKEN>`.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class CrmContact(BaseModel):
    """The WhatsApp contact behind the conversation."""

    phone: str
    name: Optional[str] = None
    person_id: Optional[int] = None
    lead_id: Optional[int] = None


class CrmMessage(BaseModel):
    """The inbound message that triggered the event."""

    model_config = ConfigDict(extra="ignore")

    id: Optional[int] = None
    type: str = "text"
    text: Optional[str] = None
    timestamp: Optional[str] = None
    # Present only when type == "flow_reply": the data the patient submitted in a WhatsApp form
    # (keys: flow, status, patient_name, relation, specialty, doctor_name, date, time, …).
    flow: Optional[dict] = None


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
        raise a ValidationError that rejected the WHOLE `message.received` event — so the agent
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


class CrmPatientContext(BaseModel):
    """Extra context the CRM attaches to each event (optional).

    `pacientes_a_cargo` lists the people this contact schedules for (hijos, padres, pareja, …) and
    `citas` carries their appointments, so the agent can answer "¿cuándo es la cita de mi hijo?"
    without sending a form. Shape is CRM-defined; kept as plain dicts (extra fields ignored).
    """

    model_config = ConfigDict(extra="ignore")

    pacientes_a_cargo: list[dict] = []
    citas: list[dict] = []


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
    patient_context: Optional[CrmPatientContext] = None
