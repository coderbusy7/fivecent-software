from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.services.plugin_yachting.domain.fleet import ActiveHold, hold_overlaps
from fivecent.services.plugin_yachting.domain.quoting import Boat, QuoteBreakdown
from fivecent.services.plugin_yachting.persistence.models import (
    BrokerAlertOutbox,
    FleetInventory,
    Hold,
    Lead,
    Quote,
)
from fivecent.services.plugin_yachting.ports import BrokerAlertPort, SoftHoldReserveResult


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


def soft_hold_idempotency_key(
    tenant_id: str,
    conversation_id: str,
    boat_id: str,
    start_date: date,
    days: int,
) -> str:
    return f"hold:{tenant_id}:{conversation_id}:{boat_id}:{start_date.isoformat()}:{days}"


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
                select(Hold)
                .where(
                    Hold.tenant_id == tenant_id,
                    Hold.boat_id == boat_id,
                    Hold.expires_at > now,
                )
                .with_for_update()
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
    ) -> SoftHoldReserveResult:
        key = soft_hold_idempotency_key(
            tenant_id, conversation_id, boat_id, start_date, days
        )
        async with self._sessions() as session, session.begin():
            existing_alert = await session.scalar(
                select(BrokerAlertOutbox).where(BrokerAlertOutbox.idempotency_key == key)
            )
            if existing_alert is not None:
                payload = json.loads(existing_alert.payload_json)
                return SoftHoldReserveResult(
                    status="reserved",
                    lead_id=str(payload.get("lead_id") or ""),
                    hold_id=str(payload.get("hold_id") or ""),
                    alert_id=existing_alert.id,
                    expires_at=(
                        datetime.fromisoformat(payload["expires_at"])
                        if payload.get("expires_at")
                        else expires_at
                    ),
                    reused=True,
                    alert_status=existing_alert.status,
                    payload=payload,
                )

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
                return SoftHoldReserveResult(status="unknown_boat")

            if quote_id:
                quote = await session.scalar(
                    select(Quote).where(Quote.tenant_id == tenant_id, Quote.id == quote_id)
                )
                if quote is None:
                    return SoftHoldReserveResult(status="unknown_quote")
                if (
                    quote.boat_id != boat_id
                    or quote.start_date != start_date
                    or quote.days != days
                    or quote.conversation_id != conversation_id
                ):
                    return SoftHoldReserveResult(status="quote_mismatch")

            result = await session.execute(
                select(Hold)
                .where(
                    Hold.tenant_id == tenant_id,
                    Hold.boat_id == boat_id,
                    Hold.expires_at > now,
                )
                .with_for_update()
            )
            active_holds = list(result.scalars().all())
            overlap = any(
                hold_overlaps(
                    ActiveHold(
                        boat_id=row.boat_id,
                        start_date=row.start_date,
                        days=row.days,
                        expires_at=_as_utc(row.expires_at),
                    ),
                    start_date,
                    days,
                    now,
                )
                for row in active_holds
            )
            if overlap:
                return SoftHoldReserveResult(status="conflict")

            lead_id = str(uuid4())
            hold_id = str(uuid4())
            alert_id = str(uuid4())
            stamped = dict(alert_payload)
            stamped["lead_id"] = lead_id
            stamped["hold_id"] = hold_id
            stamped["alert_id"] = alert_id
            stamped["expires_at"] = expires_at.isoformat()
            now_utc = datetime.now(timezone.utc)
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
                    created_at=now_utc,
                )
            )
            session.add(
                Hold(
                    id=hold_id,
                    tenant_id=tenant_id,
                    boat_id=boat_id,
                    start_date=start_date,
                    days=days,
                    expires_at=expires_at,
                    lead_id=lead_id,
                    conversation_id=conversation_id,
                    created_at=now_utc,
                )
            )
            session.add(
                BrokerAlertOutbox(
                    id=alert_id,
                    tenant_id=tenant_id,
                    kind="hold",
                    idempotency_key=key,
                    payload_json=json.dumps(stamped),
                    status="pending",
                    attempts=0,
                    last_error=None,
                    created_at=now_utc,
                    updated_at=now_utc,
                    completed_at=None,
                )
            )
            return SoftHoldReserveResult(
                status="reserved",
                lead_id=lead_id,
                hold_id=hold_id,
                alert_id=alert_id,
                expires_at=expires_at,
                reused=False,
                alert_status="pending",
                payload=stamped,
            )


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


class SqlAlertOutboxRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def mark_completed(self, alert_id: str) -> None:
        async with self._sessions() as session:
            row = await session.get(BrokerAlertOutbox, alert_id)
            if row is None:
                return
            now = datetime.now(timezone.utc)
            row.status = "completed"
            row.completed_at = now
            row.updated_at = now
            row.last_error = None
            row.attempts = int(row.attempts or 0) + 1
            await session.commit()

    async def mark_failed(self, alert_id: str, error: str) -> None:
        async with self._sessions() as session:
            row = await session.get(BrokerAlertOutbox, alert_id)
            if row is None:
                return
            row.status = "pending"
            row.updated_at = datetime.now(timezone.utc)
            row.last_error = error[:2000]
            row.attempts = int(row.attempts or 0) + 1
            await session.commit()

    async def list_pending(self, *, limit: int = 50) -> Sequence[dict[str, Any]]:
        async with self._sessions() as session:
            result = await session.execute(
                select(BrokerAlertOutbox)
                .where(BrokerAlertOutbox.status == "pending")
                .order_by(BrokerAlertOutbox.created_at.asc())
                .limit(limit)
            )
            return [
                {
                    "id": row.id,
                    "tenant_id": row.tenant_id,
                    "kind": row.kind,
                    "payload": json.loads(row.payload_json),
                    "attempts": row.attempts,
                    "last_error": row.last_error,
                }
                for row in result.scalars().all()
            ]

    async def flush_pending(
        self, alerts: BrokerAlertPort, *, limit: int = 50
    ) -> dict[str, int]:
        pending = await self.list_pending(limit=limit)
        delivered = 0
        failed = 0
        for item in pending:
            kind = item["kind"]
            tenant_id = item["tenant_id"]
            payload = item["payload"]
            try:
                if kind == "hold":
                    ok = await alerts.notify_hold(tenant_id, payload)
                elif kind == "followup":
                    ok = await alerts.notify_followup(tenant_id, payload)
                else:
                    ok = await alerts.notify_handoff(tenant_id, payload)
            except Exception as exc:
                await self.mark_failed(item["id"], str(exc))
                failed += 1
                continue
            if ok:
                await self.mark_completed(item["id"])
                delivered += 1
            else:
                await self.mark_failed(item["id"], "one or more alert channels failed")
                failed += 1
        return {"attempted": len(pending), "delivered": delivered, "failed": failed}
