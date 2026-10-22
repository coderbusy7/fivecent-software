"""Single-use Telegram desk invite tokens.

Revision ID: 0007_telegram_desk_invites
Revises: 0006_soft_hold_atomic
Create Date: 2026-10-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_telegram_desk_invites"
down_revision: Union[str, Sequence[str], None] = "0006_soft_hold_atomic"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "telegram_desk_invites" in tables:
        return
    op.create_table(
        "telegram_desk_invites",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_telegram_desk_invites_tenant_id", "telegram_desk_invites", ["tenant_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "telegram_desk_invites" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_telegram_desk_invites_tenant_id", table_name="telegram_desk_invites")
    op.drop_table("telegram_desk_invites")
