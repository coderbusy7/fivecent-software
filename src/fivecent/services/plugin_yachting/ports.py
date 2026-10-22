from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Optional, Protocol, Sequence

from fivecent.services.plugin_yachting.domain.fleet import ActiveHold
from fivecent.services.plugin_yachting.domain.quoting import Boat, QuoteBreakdown

SoftHoldStatus = Literal[
    "reserved",
    "conflict",
    "unknown_boat",
    "unknown_quote",
    "quote_mismatch",
]


@dataclass(frozen=True)
class SoftHoldReserveResult:
    status: SoftHoldStatus
    lead_id: Optional[str] = None
    hold_id: Optional[str] = None
    alert_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    reused: bool = False
    alert_status: Optional[str] = None
    payload: Optional[dict[str, Any]] = None


class BrokerAlertPort(Protocol):
    async def notify_hold(self, tenant_id: str, payload: dict) -> bool: ...

    async def notify_handoff(self, tenant_id: str, payload: dict) -> bool: ...

    async def notify_followup(self, tenant_id: str, payload: dict) -> bool: ...


class BrokerAlertChannel(Protocol):
    """Transport sink for broker alerts. Chatwoot (etc.) implements this later."""

    async def deliver(self, tenant_id: str, kind: str, payload: dict) -> None: ...


class FleetRepo(Protocol):
    async def list_active(self, tenant_id: str) -> Sequence[Boat]: ...

    async def get(self, tenant_id: str, boat_id: str) -> Optional[Boat]: ...


class HoldRepo(Protocol):
    async def list_active(self, tenant_id: str, now: datetime) -> Sequence[ActiveHold]: ...

    async def create_if_available(
        self,
        *,
        tenant_id: str,
        boat_id: str,
        start_date: date,
        days: int,
        now: datetime,
        expires_at: datetime,
        lead_id: str,
    ) -> bool: ...

    async def reserve_soft_hold(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        customer_name: str,
        email: str,
        boat_id: str,
        start_date: date,
        days: int,
        now: datetime,
        expires_at: datetime,
        quote_id: Optional[str],
        alert_payload: dict[str, Any],
    ) -> SoftHoldReserveResult: ...


class QuoteRepo(Protocol):
    async def save(
        self, *, tenant_id: str, conversation_id: str, quote_id: str, quote: QuoteBreakdown
    ) -> None: ...

    async def get(self, tenant_id: str, quote_id: str) -> Optional[QuoteBreakdown]: ...


class LeadRepo(Protocol):
    async def upsert_hold_lead(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        customer_name: str,
        email: str,
        boat_id: str,
        start_date: date,
        days: int,
        quote_id: Optional[str],
    ) -> str: ...


class AlertOutboxRepo(Protocol):
    async def mark_completed(self, alert_id: str) -> None: ...

    async def mark_failed(self, alert_id: str, error: str) -> None: ...

    async def list_pending(self, *, limit: int = 50) -> Sequence[dict[str, Any]]: ...

    async def flush_pending(
        self, alerts: BrokerAlertPort, *, limit: int = 50
    ) -> dict[str, int]: ...
