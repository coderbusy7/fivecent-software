"""Add tenant service and LLM selection.

Revision ID: 0003_tenant_plugins
Revises: 0002_tenant_config
Create Date: 2026-10-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_tenant_plugins"
down_revision: Union[str, Sequence[str], None] = "0002_tenant_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tenants")}
    additions = (
        ("service_type", sa.Column("service_type", sa.String(64), nullable=False, server_default="yachting")),
        ("llm_provider", sa.Column("llm_provider", sa.String(64), nullable=False, server_default="openai")),
        ("llm_model", sa.Column("llm_model", sa.String(128), nullable=False, server_default="gpt-4o-mini")),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("tenants", column)


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tenants")}
    for name in ("llm_model", "llm_provider", "service_type"):
        if name in columns:
            op.drop_column("tenants", name)
