"""Named admin users and audit history (ED-6)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.adapters.persistence.models import AdminAuditLog, AdminUser
from fivecent.auth.roles import Actor, hash_api_token, mint_api_token, token_matches


class SqlUserRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_users(self, *, tenant_id: Optional[str] = None) -> Sequence[dict]:
        async with self._sessions() as session:
            stmt = select(AdminUser).order_by(AdminUser.created_at.desc())
            if tenant_id:
                stmt = stmt.where(AdminUser.tenant_id == tenant_id)
            rows = (await session.execute(stmt)).scalars().all()
        return [_serialize(row) for row in rows]

    async def create_user(
        self,
        *,
        email: str,
        display_name: str,
        role: str,
        tenant_id: Optional[str],
    ) -> dict:
        token = mint_api_token()
        user_id = str(uuid4())
        async with self._sessions() as session:
            session.add(
                AdminUser(
                    id=user_id,
                    email=email.strip().lower(),
                    display_name=display_name.strip(),
                    role=role,
                    tenant_id=tenant_id,
                    password_hash=None,
                    api_token_hash=hash_api_token(token),
                    is_active=True,
                    created_at=datetime.now(timezone.utc),
                    disabled_at=None,
                )
            )
            await session.commit()
        return {**_serialize_from_fields(user_id, email, display_name, role, tenant_id, True), "api_token": token}

    async def disable_user(self, user_id: str) -> Optional[dict]:
        async with self._sessions() as session:
            row = await session.get(AdminUser, user_id)
            if row is None:
                return None
            row.is_active = False
            row.disabled_at = datetime.now(timezone.utc)
            row.api_token_hash = None
            await session.commit()
            return _serialize(row)

    async def resolve_bearer(self, token: str) -> Optional[Actor]:
        digest = hash_api_token(token)
        async with self._sessions() as session:
            row = await session.scalar(
                select(AdminUser).where(
                    AdminUser.api_token_hash == digest,
                    AdminUser.is_active.is_(True),
                )
            )
        if row is None:
            return None
        return Actor(
            id=row.id,
            email=row.email,
            role=row.role,  # type: ignore[arg-type]
            tenant_id=row.tenant_id,
        )

    async def record_audit(
        self,
        *,
        actor: Optional[Actor],
        tenant_id: Optional[str],
        action: str,
        resource_type: str,
        resource_id: Optional[str],
        before: Optional[dict[str, Any]] = None,
        after: Optional[dict[str, Any]] = None,
    ) -> None:
        async with self._sessions() as session:
            session.add(
                AdminAuditLog(
                    id=str(uuid4()),
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None,
                    tenant_id=tenant_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    before_json=_safe_json(before),
                    after_json=_safe_json(after),
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    async def list_audit(
        self, *, tenant_id: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        async with self._sessions() as session:
            stmt = select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit)
            if tenant_id:
                stmt = stmt.where(AdminAuditLog.tenant_id == tenant_id)
            rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": row.id,
                "actor_id": row.actor_id,
                "actor_email": row.actor_email,
                "tenant_id": row.tenant_id,
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "before": row.before_json,
                "after": row.after_json,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]


_SECRET_KEYS = {"password", "password_hash", "api_token", "token", "access_token", "secret"}


def _safe_json(payload: Optional[dict[str, Any]]) -> Optional[str]:
    if payload is None:
        return None
    cleaned = {
        key: ("[redacted]" if key.lower() in _SECRET_KEYS else value)
        for key, value in payload.items()
    }
    return json.dumps(cleaned, default=str)


def _serialize(row: AdminUser) -> dict:
    return _serialize_from_fields(
        row.id, row.email, row.display_name, row.role, row.tenant_id, row.is_active
    )


def _serialize_from_fields(
    user_id: str,
    email: str,
    display_name: str,
    role: str,
    tenant_id: Optional[str],
    is_active: bool,
) -> dict:
    return {
        "user_id": user_id,
        "email": email,
        "display_name": display_name,
        "role": role,
        "tenant_id": tenant_id,
        "is_active": is_active,
    }


# Re-export for tests that mint tokens
__all__ = ["SqlUserRepo", "token_matches", "hash_api_token", "mint_api_token"]
