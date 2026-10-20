"""Per-tenant financial rules, flags, and brokers.

Revision ID: 0002_tenant_config
Revises: 0001_initial
Create Date: 2026-10-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_tenant_config"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("tenants")}
    if "agency_name" not in columns and "name" in columns:
        op.alter_column("tenants", "name", new_column_name="agency_name")
        columns.add("agency_name")
    additions = [
        ("currency", sa.Column("currency", sa.String(8), server_default="EUR")),
        ("whatsapp_waba_id", sa.Column("whatsapp_waba_id", sa.String(64), server_default="")),
        ("default_vat_rate", sa.Column("default_vat_rate", sa.Numeric(4, 2), server_default="0.20")),
        ("default_apa_rate", sa.Column("default_apa_rate", sa.Numeric(4, 2), server_default="0.30")),
        ("max_discount_pct", sa.Column("max_discount_pct", sa.Numeric(4, 2), server_default="0.08")),
        ("routing_mode", sa.Column("routing_mode", sa.String(32), server_default="broadcast")),
        ("feature_flags", sa.Column("feature_flags", sa.JSON(), nullable=True)),
        ("is_active", sa.Column("is_active", sa.Boolean(), server_default=sa.true())),
        ("created_at", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True)),
    ]
    for name, column in additions:
        if name not in columns:
            op.add_column("tenants", column)
    if "brokers" not in inspector.get_table_names():
        op.create_table(
            "brokers",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("phone_number", sa.String(32), nullable=False),
            sa.Column("telegram_chat_id", sa.String(64)),
            sa.Column("role", sa.String(32), server_default="broker"),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True)),
        )


def downgrade() -> None:
    op.drop_table("brokers")
