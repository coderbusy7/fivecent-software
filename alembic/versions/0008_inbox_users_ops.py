"""Inbox workflow, named users, audit log, and usage/retention hooks.

Revision ID: 0008_inbox_users_ops
Revises: 0007_telegram_desk_invites
Create Date: 2026-10-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_inbox_users_ops"
down_revision: Union[str, Sequence[str], None] = "0007_telegram_desk_invites"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "conversations" in tables:
        cols = {c["name"] for c in inspector.get_columns("conversations")}
        if "assigned_broker_id" not in cols:
            op.add_column(
                "conversations",
                sa.Column("assigned_broker_id", sa.String(64), nullable=True),
            )
        if "handoff_status" not in cols:
            op.add_column(
                "conversations",
                sa.Column(
                    "handoff_status",
                    sa.String(32),
                    nullable=False,
                    server_default="open",
                ),
            )
        if "updated_at" not in cols:
            op.add_column(
                "conversations",
                sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            )

    if "admin_users" not in tables:
        op.create_table(
            "admin_users",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("email", sa.String(255), nullable=False, unique=True),
            sa.Column("display_name", sa.String(255), nullable=False),
            sa.Column("role", sa.String(32), nullable=False),
            sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=True),
            sa.Column("password_hash", sa.String(255), nullable=True),
            sa.Column("api_token_hash", sa.String(128), nullable=True, unique=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_admin_users_tenant_id", "admin_users", ["tenant_id"])

    if "admin_audit_log" not in tables:
        op.create_table(
            "admin_audit_log",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("actor_id", sa.String(64), nullable=True),
            sa.Column("actor_email", sa.String(255), nullable=True),
            sa.Column("tenant_id", sa.String(64), nullable=True),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("resource_type", sa.String(64), nullable=False),
            sa.Column("resource_id", sa.String(128), nullable=True),
            sa.Column("before_json", sa.Text(), nullable=True),
            sa.Column("after_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_admin_audit_log_tenant_id", "admin_audit_log", ["tenant_id"])
        op.create_index("ix_admin_audit_log_created_at", "admin_audit_log", ["created_at"])

    if "tenant_usage_daily" not in tables:
        op.create_table(
            "tenant_usage_daily",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("day", sa.String(10), nullable=False),
            sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("turns", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        )
        op.create_index(
            "ix_tenant_usage_daily_tenant_day",
            "tenant_usage_daily",
            ["tenant_id", "day"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "tenant_usage_daily" in tables:
        op.drop_table("tenant_usage_daily")
    if "admin_audit_log" in tables:
        op.drop_table("admin_audit_log")
    if "admin_users" in tables:
        op.drop_table("admin_users")
    if "conversations" in tables:
        cols = {c["name"] for c in inspector.get_columns("conversations")}
        for name in ("updated_at", "handoff_status", "assigned_broker_id"):
            if name in cols:
                op.drop_column("conversations", name)
