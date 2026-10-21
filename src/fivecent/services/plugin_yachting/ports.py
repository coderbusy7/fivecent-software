from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Protocol, Sequence

from fivecent.services.plugin_yachting.domain.fleet import ActiveHold
from fivecent.services.plugin_yachting.domain.quoting import Boat, QuoteBreakdown


class BrokerAlertPort(Protocol):
    async def notify_hold(self, tenant_id: str, payload: dict) -> None: ...


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
