"""Track inbound message processing status for retryable delivery.

Revision ID: 0005_message_processing
Revises: 0004_tenant_telegram
Create Date: 2026-10-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_message_processing"
down_revision: Union[str, Sequence[str], None] = "0004_tenant_telegram"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "messages" in tables:
        message_cols = {column["name"] for column in inspector.get_columns("messages")}
        if "processing_status" not in message_cols:
            op.add_column(
                "messages",
                sa.Column(
                    "processing_status",
                    sa.String(32),
                    nullable=False,
                    server_default="completed",
                ),
            )
        if "skip_reason" not in message_cols:
            op.add_column(
                "messages", sa.Column("skip_reason", sa.String(64), nullable=True)
            )

    if "inbound_payloads" in tables:
        payload_cols = {
            column["name"] for column in inspector.get_columns("inbound_payloads")
        }
        if "status" not in payload_cols:
            op.add_column(
                "inbound_payloads",
                sa.Column(
                    "status", sa.String(32), nullable=False, server_default="accepted"
                ),
            )
        if "error" not in payload_cols:
            op.add_column(
                "inbound_payloads", sa.Column("error", sa.Text(), nullable=True)
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "messages" in tables:
        message_cols = {column["name"] for column in inspector.get_columns("messages")}
        if "skip_reason" in message_cols:
            op.drop_column("messages", "skip_reason")
        if "processing_status" in message_cols:
            op.drop_column("messages", "processing_status")

    if "inbound_payloads" in tables:
        payload_cols = {
            column["name"] for column in inspector.get_columns("inbound_payloads")
        }
        if "error" in payload_cols:
            op.drop_column("inbound_payloads", "error")
        if "status" in payload_cols:
            op.drop_column("inbound_payloads", "status")
