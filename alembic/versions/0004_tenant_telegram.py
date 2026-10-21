"""Add tenant-level telegram_chat_id for dispatch bot onboarding.

Revision ID: 0004_tenant_telegram
Revises: 0003_tenant_plugins
Create Date: 2026-10-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_tenant_telegram"
down_revision: Union[str, Sequence[str], None] = "0003_tenant_plugins"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tenants")}
    if "telegram_chat_id" not in columns:
        op.add_column("tenants", sa.Column("telegram_chat_id", sa.String(64), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tenants")}
    if "telegram_chat_id" in columns:
        op.drop_column("tenants", "telegram_chat_id")
