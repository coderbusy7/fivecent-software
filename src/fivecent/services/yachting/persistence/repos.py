from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.services.yachting.domain.fleet import ActiveHold, hold_overlaps
from fivecent.services.yachting.domain.quoting import Boat, QuoteBreakdown
from fivecent.services.yachting.persistence.models import FleetInventory, Hold, Lead, Quote


def _boat(row: FleetInventory) -> Boat:
    return Boat(
        id=row.id,
        name=row.name,
        length_m=Decimal(row.length_m),
        capacity=row.capacity,
        low_season_daily_rate=Decimal(row.low_season_daily_rate),
        high_season_daily_rate=Decimal(row.high_season_daily_rate),
        skipper_daily_rate=Decimal(row.skipper_daily_rate),
        active=row.active,
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class SqlFleetRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_active(self, tenant_id: str) -> Sequence[Boat]:
        async with self._sessions() as session:
            result = await session.execute(
                select(FleetInventory).where(
                    FleetInventory.tenant_id == tenant_id,
                    FleetInventory.active.is_(True),
                )
            )
            return [_boat(row) for row in result.scalars().all()]

    async def get(self, tenant_id: str, boat_id: str) -> Optional[Boat]:
        async with self._sessions() as session:
            result = await session.execute(
                select(FleetInventory).where(
                    FleetInventory.tenant_id == tenant_id,
                    FleetInventory.id == boat_id,
                )
            )
            row = result.scalar_one_or_none()
            return _boat(row) if row else None


class SqlHoldRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_active(self, tenant_id: str, now: datetime) -> Sequence[ActiveHold]:
        async with self._sessions() as session:
            result = await session.execute(
                select(Hold).where(Hold.tenant_id == tenant_id, Hold.expires_at > now)
            )
            return [
                ActiveHold(
                    boat_id=row.boat_id,
                    start_date=row.start_date,
                    days=row.days,
                    expires_at=_as_utc(row.expires_at),
                )
                for row in result.scalars().all()
            ]

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
    ) -> bool:
        async with self._sessions() as session, session.begin():
            boat = await session.scalar(
                select(FleetInventory)
                .where(
                    FleetInventory.id == boat_id,
                    FleetInventory.tenant_id == tenant_id,
                    FleetInventory.active.is_(True),
                )
                .with_for_update()
            )
            if boat is None:
                return False
            result = await session.execute(
                select(Hold).where(
                    Hold.tenant_id == tenant_id,
                    Hold.boat_id == boat_id,
                    Hold.expires_at > now,
                )
            )
            active_holds = [
                ActiveHold(
                    boat_id=row.boat_id,
                    start_date=row.start_date,
                    days=row.days,
                    expires_at=_as_utc(row.expires_at),
                )
                for row in result.scalars().all()
            ]
            if any(hold_overlaps(hold, start_date, days, now) for hold in active_holds):
                return False
            session.add(
                Hold(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    boat_id=boat_id,
                    start_date=start_date,
                    days=days,
                    expires_at=expires_at,
                    lead_id=lead_id,
                    created_at=datetime.now(timezone.utc),
                )
            )
        return True


class SqlQuoteRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(
        self, *, tenant_id: str, conversation_id: str, quote_id: str, quote: QuoteBreakdown
    ) -> None:
        async with self._sessions() as session:
            session.add(
                Quote(
                    id=quote_id,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    boat_id=quote.boat_id,
                    start_date=quote.start_date,
                    days=quote.days,
                    season=quote.season,
                    skipper_included=quote.skipper_included,
                    daily_rate=quote.daily_rate,
                    base=quote.base,
                    skipper=quote.skipper,
                    vat=quote.vat,
                    apa=quote.apa,
                    total=quote.total,
                    currency=quote.currency,
                    policy_version=quote.policy_version,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    async def get(self, tenant_id: str, quote_id: str) -> Optional[QuoteBreakdown]:
        async with self._sessions() as session:
            result = await session.execute(
                select(Quote).where(Quote.tenant_id == tenant_id, Quote.id == quote_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            boat = await session.get(FleetInventory, row.boat_id)
            return QuoteBreakdown(
                boat_id=row.boat_id,
                boat_name=boat.name if boat else row.boat_id,
                start_date=row.start_date,
                days=row.days,
                season=row.season,  # type: ignore[arg-type]
                daily_rate=Decimal(row.daily_rate),
                skipper_included=row.skipper_included,
                base=Decimal(row.base),
                skipper=Decimal(row.skipper),
                vat=Decimal(row.vat),
                apa=Decimal(row.apa),
                total=Decimal(row.total),
                currency=row.currency,  # type: ignore[arg-type]
                policy_version=row.policy_version,
            )


class SqlLeadRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

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
    ) -> str:
        lead_id = str(uuid4())
        async with self._sessions() as session:
            session.add(
                Lead(
                    id=lead_id,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    customer_name=customer_name,
                    email=email,
                    boat_id=boat_id,
                    start_date=start_date,
                    days=days,
                    quote_id=quote_id,
                    state="option_held",
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        return lead_id
