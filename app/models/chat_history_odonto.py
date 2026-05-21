"""SQLModel for WhatsApp chat history per session."""

from datetime import UTC, datetime

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class ChatHistoryOdonto(SQLModel, table=True):
    """One row per message in a WhatsApp conversation."""

    __tablename__ = "chat_histories_odonto"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)
    tenant_slug: str = Field(index=True)
    session_id: str = Field(index=True)
    message: str = Field(sa_column=Column(Text))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
