"""Fase 4 - Multitenancy platform: tenants, usage_logs, user.is_admin.

Revision ID: c3f1a2b4d5e6
Revises: 352d5a24eefc
Create Date: 2026-05-14

Changes:
  + Table  tenants      - per-client WhatsApp + CRM + billing config
  + Table  usage_logs   - daily message/token counters per tenant
  + Column user.is_admin BOOLEAN NOT NULL DEFAULT FALSE
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

revision: str = "c3f1a2b4d5e6"
down_revision: Union[str, Sequence[str], None] = "352d5a24eefc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tenants ──────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("phone_number_id", sa.String(64), nullable=False),
        sa.Column("wa_access_token", sa.String(2048), nullable=False),
        sa.Column("verify_token", sa.String(512), nullable=False),
        sa.Column("agent_type", sa.String(64), nullable=False),
        sa.Column("llm_model", sa.String(64), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("crm_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("crm_token", sa.String(2048), nullable=False, server_default=""),
        sa.Column("billing_model", sa.String(16), nullable=False, server_default="fixed"),
        sa.Column("billing_tier", sa.String(16), nullable=False, server_default="local"),
        sa.Column("msg_limit_month", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
        sa.UniqueConstraint("phone_number_id"),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"])

    # ── usage_logs ───────────────────────────────────────────────────────────
    op.create_table(
        "usage_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("msg_received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("msg_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("msg_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=4), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "log_date", name="uq_usage_logs_tenant_date"),
    )
    op.create_index("ix_usage_logs_tenant_id", "usage_logs", ["tenant_id"])
    op.create_index("ix_usage_logs_log_date", "usage_logs", ["log_date"])

    # ── user.is_admin ─────────────────────────────────────────────────────────
    op.add_column(
        "user",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("user", "is_admin")
    op.drop_table("usage_logs")
    op.drop_table("tenants")
