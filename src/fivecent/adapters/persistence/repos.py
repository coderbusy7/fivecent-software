from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.adapters.persistence.models import (
    Conversation,
    FleetInventory,
    Hold,
    InboundPayload,
    Lead,
    Message,
    Quote,
)
from fivecent.adapters.persistence.tenants import SqlTenantRepo
from fivecent.domain.events import InboundMessageEvent
from fivecent.domain.fleet import ActiveHold
from fivecent.domain.quoting import Boat, QuoteBreakdown

__all__ = ["SqlTenantRepo", "SqlFleetRepo", "SqlHoldRepo", "SqlQuoteRepo", "SqlLeadRepo", "SqlConversationRepo"]


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
                    expires_at=row.expires_at,
                )
                for row in result.scalars().all()
            ]

    async def create(
        self,
        *,
        tenant_id: str,
        boat_id: str,
        start_date: date,
        days: int,
        expires_at: datetime,
        lead_id: str,
    ) -> None:
        async with self._sessions() as session:
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
            await session.commit()


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


class SqlConversationRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def _get_or_create_conversation(
        self, session: AsyncSession, event: InboundMessageEvent
    ) -> Conversation:
        result = await session.execute(
            select(Conversation).where(
                Conversation.tenant_id == event.tenant_id,
                Conversation.channel == event.channel,
                Conversation.user_channel_id == event.user_channel_id,
            )
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            conv = Conversation(
                id=str(uuid4()),
                tenant_id=event.tenant_id,
                channel=event.channel,
                user_channel_id=event.user_channel_id,
            )
            session.add(conv)
            await session.flush()
        return conv

    async def claim_inbound(self, event: InboundMessageEvent, role: str) -> Optional[str]:
        async with self._sessions() as session:
            conv = await self._get_or_create_conversation(session, event)
            session.add(
                Message(
                    id=str(uuid4()),
                    tenant_id=event.tenant_id,
                    conversation_id=conv.id,
                    role=role,
                    text=event.text,
                    provider_message_id=event.provider_message_id,
                    created_at=event.timestamp,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            return conv.id

    async def recent_turns(
        self, tenant_id: str, channel: str, user_channel_id: str, limit: int = 15
    ) -> list[dict]:
        async with self._sessions() as session:
            conv_result = await session.execute(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.channel == channel,
                    Conversation.user_channel_id == user_channel_id,
                )
            )
            conv = conv_result.scalar_one_or_none()
            if conv is None:
                return []
            result = await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conv.id,
                    Message.role.in_(("user", "assistant")),
                )
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            rows = list(reversed(result.scalars().all()))
            return [{"role": row.role, "content": row.text or ""} for row in rows]

    async def append_assistant(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        text: str,
        provider_message_id: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
    ) -> None:
        async with self._sessions() as session:
            session.add(
                Message(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    text=text,
                    provider_message_id=provider_message_id,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    async def record_raw_inbound(
        self, *, tenant_id: Optional[str], channel: str, body: str
    ) -> None:
        async with self._sessions() as session:
            session.add(
                InboundPayload(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    channel=channel,
                    body=body,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
