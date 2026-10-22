"""Client portal invite tokens for password provisioning (ED-15).

Revision ID: 0009_client_portal_invites
Revises: 0008_inbox_users_ops
Create Date: 2026-10-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_client_portal_invites"
down_revision: Union[str, Sequence[str], None] = "0008_inbox_users_ops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "admin_users" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("admin_users")}
    if "invite_token_hash" in cols and "invite_expires_at" in cols:
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("admin_users") as batch:
            if "invite_token_hash" not in cols:
                batch.add_column(
                    sa.Column("invite_token_hash", sa.String(128), nullable=True)
                )
            if "invite_expires_at" not in cols:
                batch.add_column(
                    sa.Column(
                        "invite_expires_at", sa.DateTime(timezone=True), nullable=True
                    )
                )
    else:
        if "invite_token_hash" not in cols:
            op.add_column(
                "admin_users",
                sa.Column("invite_token_hash", sa.String(128), nullable=True),
            )
        if "invite_expires_at" not in cols:
            op.add_column(
                "admin_users",
                sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True),
            )

    inspector = sa.inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes("admin_users")}
    if "uq_admin_users_invite_token_hash" not in indexes:
        op.create_index(
            "uq_admin_users_invite_token_hash",
            "admin_users",
            ["invite_token_hash"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "admin_users" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("admin_users")}
    indexes = {idx["name"] for idx in inspector.get_indexes("admin_users")}
    if "uq_admin_users_invite_token_hash" in indexes:
        op.drop_index("uq_admin_users_invite_token_hash", table_name="admin_users")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("admin_users") as batch:
            if "invite_expires_at" in cols:
                batch.drop_column("invite_expires_at")
            if "invite_token_hash" in cols:
                batch.drop_column("invite_token_hash")
    else:
        for name in ("invite_expires_at", "invite_token_hash"):
            if name in cols:
                op.drop_column("admin_users", name)
