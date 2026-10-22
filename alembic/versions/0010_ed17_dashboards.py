"""Durable conversation mute for ED-17 dashboards.

Revision ID: 0010_ed17_dashboards
Revises: 0009_client_portal_invites
Create Date: 2026-10-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_ed17_dashboards"
down_revision: Union[str, Sequence[str], None] = "0009_client_portal_invites"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "conversations" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("conversations")}
    if "bot_muted_until" in cols:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("conversations") as batch:
            batch.add_column(
                sa.Column("bot_muted_until", sa.DateTime(timezone=True), nullable=True)
            )
    else:
        op.add_column(
            "conversations",
            sa.Column("bot_muted_until", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "conversations" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("conversations")}
    if "bot_muted_until" not in cols:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("conversations") as batch:
            batch.drop_column("bot_muted_until")
    else:
        op.drop_column("conversations", "bot_muted_until")
