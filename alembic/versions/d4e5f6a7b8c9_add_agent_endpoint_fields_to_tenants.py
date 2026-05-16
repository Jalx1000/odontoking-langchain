"""Add agent_endpoint_url and agent_api_key to tenants.

Revision ID: d4e5f6a7b8c9
Revises: c3f1a2b4d5e6
Create Date: 2026-05-15

Changes:
  + Column  tenants.agent_endpoint_url  VARCHAR(512)  NULL  — URL of the agent service endpoint
  + Column  tenants.agent_api_key       VARCHAR(2048) NULL  — API key for the agent service (encrypted at rest)
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3f1a2b4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("agent_endpoint_url", sa.String(length=512), nullable=True))
    op.add_column("tenants", sa.Column("agent_api_key", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "agent_api_key")
    op.drop_column("tenants", "agent_endpoint_url")
