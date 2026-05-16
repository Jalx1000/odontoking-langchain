"""Tenant SQLModel — persisted client configuration for the agency platform."""

import uuid
from datetime import datetime, UTC
from typing import Optional

from sqlmodel import Field, SQLModel


class Tenant(SQLModel, table=True):
    """One row per agency client. Sensitive token fields are Fernet-encrypted at rest."""

    __tablename__ = "tenants"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    slug: str = Field(unique=True, max_length=64, index=True)
    display_name: str = Field(max_length=255)

    # Meta / WhatsApp credentials (encrypted at rest)
    phone_number_id: str = Field(unique=True, max_length=64)
    wa_access_token: str = Field(max_length=2048)   # encrypted
    verify_token: str = Field(max_length=512)        # encrypted

    # Agent configuration
    agent_type: str = Field(max_length=64)           # "odontoking" | "kohlberg"
    llm_model: str = Field(default="gpt-4o-mini", max_length=64)
    agent_endpoint_url: Optional[str] = Field(default=None, max_length=512)
    agent_api_key: Optional[str] = Field(default=None, max_length=2048)

    # CRM / external API (encrypted at rest)
    crm_url: str = Field(default="", max_length=512)
    crm_token: str = Field(default="", max_length=2048)  # encrypted

    # Billing
    billing_model: str = Field(default="fixed", max_length=16)     # "fixed" | "volume"
    billing_tier: str = Field(default="local", max_length=16)      # "local" | "national" | "enterprise"
    msg_limit_month: int = Field(default=5000)

    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
