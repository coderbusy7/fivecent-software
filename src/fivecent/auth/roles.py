"""Named-user roles and tenant authorization (ED-6)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Literal, Optional

Role = Literal["platform_admin", "agency_admin", "broker"]

ROLE_RANK = {
    "broker": 1,
    "agency_admin": 2,
    "platform_admin": 3,
}


@dataclass(frozen=True)
class Actor:
    id: str
    email: str
    role: Role
    tenant_id: Optional[str] = None

    def can(self, minimum: Role) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK.get(minimum, 99)

    def may_access_tenant(self, tenant_id: str) -> bool:
        if self.role == "platform_admin":
            return True
        return bool(self.tenant_id) and self.tenant_id == tenant_id


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_api_token() -> str:
    return secrets.token_urlsafe(32)


def token_matches(token: str, expected_hash: str | None) -> bool:
    if not expected_hash:
        return False
    return hmac.compare_digest(hash_api_token(token), expected_hash)


PLATFORM_ACTOR = Actor(
    id="env-platform",
    email="platform@local",
    role="platform_admin",
    tenant_id=None,
)
