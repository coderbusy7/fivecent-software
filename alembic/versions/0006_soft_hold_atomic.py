"""Atomic soft-hold: conversation on holds + durable broker alert outbox.

Revision ID: 0006_soft_hold_atomic
Revises: 0005_message_processing
Create Date: 2026-10-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_soft_hold_atomic"
down_revision: Union[str, Sequence[str], None] = "0005_message_processing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "holds" in tables:
        hold_cols = {column["name"] for column in inspector.get_columns("holds")}
        if "conversation_id" not in hold_cols:
            op.add_column(
                "holds",
                sa.Column("conversation_id", sa.String(64), nullable=True),
            )
            if "conversations" in tables:
                op.create_foreign_key(
                    "fk_holds_conversation_id",
                    "holds",
                    "conversations",
                    ["conversation_id"],
                    ["id"],
                )
            op.create_index(
                "ix_holds_conversation_id", "holds", ["conversation_id"]
            )

    if "broker_alert_outbox" not in tables:
        op.create_table(
            "broker_alert_outbox",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.String(2000), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_broker_alert_outbox_tenant_id", "broker_alert_outbox", ["tenant_id"])
        op.create_index("ix_broker_alert_outbox_status", "broker_alert_outbox", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "broker_alert_outbox" in tables:
        op.drop_index("ix_broker_alert_outbox_status", table_name="broker_alert_outbox")
        op.drop_index("ix_broker_alert_outbox_tenant_id", table_name="broker_alert_outbox")
        op.drop_table("broker_alert_outbox")

    if "holds" in tables:
        hold_cols = {column["name"] for column in inspector.get_columns("holds")}
        if "conversation_id" in hold_cols:
            op.drop_index("ix_holds_conversation_id", table_name="holds")
            try:
                op.drop_constraint("fk_holds_conversation_id", "holds", type_="foreignkey")
            except Exception:
                pass
            op.drop_column("holds", "conversation_id")
