"""Named-user roles and tenant authorization (ED-6 / ED-15 / ED-17)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Literal, Optional

Role = Literal[
    "client_user",
    "agency_ops",
    "broker",
    "agency_admin",
    "platform_admin",
]

ROLE_RANK = {
    "client_user": 0,
    "agency_ops": 1,
    "broker": 2,
    "agency_admin": 3,
    "platform_admin": 4,
}

# Company admin workspace (Streamlit Admin + /internal company routes).
COMPANY_ROLES = frozenset({"broker", "agency_admin", "platform_admin"})
# Client executive portal (password login + /internal/portal/*).
PORTAL_ROLES = frozenset({"client_user", "agency_ops"})
CLIENT_ROLES = frozenset({"client_user"})


@dataclass(frozen=True)
class Actor:
    id: str
    email: str
    role: Role
    tenant_id: Optional[str] = None

    def can(self, minimum: Role) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK.get(minimum, 99)

    def is_company_staff(self) -> bool:
        return self.role in COMPANY_ROLES

    def is_client(self) -> bool:
        """Read-only portal viewer (no PII, no fleet mutation)."""
        return self.role in CLIENT_ROLES

    def is_portal_user(self) -> bool:
        """Any role that uses the client executive portal workspace."""
        return self.role in PORTAL_ROLES

    def may_view_guest_contact(self) -> bool:
        """Guest name/email on holds — agency_ops+ or company staff."""
        if self.is_company_staff():
            return True
        return self.role == "agency_ops"

    def may_mutate_fleet(self) -> bool:
        """Portal fleet maintenance / company fleet mutate."""
        if self.role == "agency_ops":
            return True
        return self.can("agency_admin")

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
