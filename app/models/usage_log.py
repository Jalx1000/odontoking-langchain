"""UsageLog SQLModel — daily message and token counters per tenant."""

import uuid
from datetime import date as date_type
from datetime import datetime, UTC

from sqlmodel import Field, SQLModel


class UsageLog(SQLModel, table=True):
    """One row per (tenant, date). Counters are upserted on each processed message."""

    __tablename__ = "usage_logs"

    id: int = Field(default=None, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id", index=True)
    log_date: date_type = Field(index=True)

    # Message counters
    msg_received: int = Field(default=0)
    msg_processed: int = Field(default=0)   # successfully replied
    msg_failed: int = Field(default=0)      # ended in DLQ

    # LLM cost tracking (populated from Langfuse or token callbacks)
    tokens_input: int = Field(default=0)
    tokens_output: int = Field(default=0)
    cost_usd: float = Field(default=0.0)    # USD, 4 decimal precision by convention

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
