"""Add tenant_slug to chat_histories_odonto.

Revision ID: e7f8a9b0c1d2
Revises: d4e5f6a7b8c9
Create Date: 2026-05-21

Changes:
  + Column  chat_histories_odonto.tenant_slug  VARCHAR(64)  NOT NULL DEFAULT 'odontoking'
  + Index   ix_chat_histories_odonto_tenant_slug
  + Backfill existing rows to tenant_slug='odontoking'
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_histories_odonto",
        sa.Column("tenant_slug", sa.String(length=64), nullable=False, server_default="odontoking"),
    )
    op.create_index(
        "ix_chat_histories_odonto_tenant_slug",
        "chat_histories_odonto",
        ["tenant_slug"],
    )
    op.execute("UPDATE chat_histories_odonto SET tenant_slug = 'odontoking' WHERE tenant_slug IS NULL")


def downgrade() -> None:
    op.drop_index("ix_chat_histories_odonto_tenant_slug", table_name="chat_histories_odonto")
    op.drop_column("chat_histories_odonto", "tenant_slug")
