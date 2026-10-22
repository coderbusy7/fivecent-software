"""Usage metering, retention, and ops snapshots (ED-13)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.adapters.persistence.models import (
    AdminAuditLog,
    InboundPayload,
    Message,
    TenantUsageDaily,
)

# Rough USD estimates for gpt-4o-mini-class models (ED-13 spend controls).
_COST_PER_1K_IN = Decimal("0.00015")
_COST_PER_1K_OUT = Decimal("0.0006")

RETENTION_DAYS = {
    "messages": 365,
    "inbound_payloads": 90,
    "audit": 730,
}


class SqlOpsRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record_turn_usage(
        self,
        tenant_id: str,
        *,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int = 0,
    ) -> dict:
        day = datetime.now(timezone.utc).date().isoformat()
        cost = (
            Decimal(tokens_in) / Decimal(1000) * _COST_PER_1K_IN
            + Decimal(tokens_out) / Decimal(1000) * _COST_PER_1K_OUT
        )
        async with self._sessions() as session:
            row = await session.scalar(
                select(TenantUsageDaily).where(
                    TenantUsageDaily.tenant_id == tenant_id,
                    TenantUsageDaily.day == day,
                )
            )
            if row is None:
                row = TenantUsageDaily(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    day=day,
                    tokens_in=0,
                    tokens_out=0,
                    turns=0,
                    estimated_cost_usd=Decimal("0"),
                )
                session.add(row)
            row.tokens_in = int(row.tokens_in or 0) + int(tokens_in)
            row.tokens_out = int(row.tokens_out or 0) + int(tokens_out)
            row.turns = int(row.turns or 0) + 1
            row.estimated_cost_usd = Decimal(str(row.estimated_cost_usd or 0)) + cost
            await session.commit()
            return {
                "tenant_id": tenant_id,
                "day": day,
                "tokens_in": row.tokens_in,
                "tokens_out": row.tokens_out,
                "turns": row.turns,
                "estimated_cost_usd": str(row.estimated_cost_usd),
                "latency_ms": latency_ms,
            }

    async def usage_for_tenant(
        self, tenant_id: str, *, days: int = 30
    ) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).date().isoformat()
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(TenantUsageDaily)
                    .where(
                        TenantUsageDaily.tenant_id == tenant_id,
                        TenantUsageDaily.day >= cutoff,
                    )
                    .order_by(TenantUsageDaily.day.desc())
                )
            ).scalars().all()
        return [
            {
                "tenant_id": row.tenant_id,
                "day": row.day,
                "tokens_in": row.tokens_in,
                "tokens_out": row.tokens_out,
                "turns": row.turns,
                "estimated_cost_usd": str(row.estimated_cost_usd),
            }
            for row in rows
        ]

    async def today_tokens(self, tenant_id: str) -> int:
        day = datetime.now(timezone.utc).date().isoformat()
        async with self._sessions() as session:
            row = await session.scalar(
                select(TenantUsageDaily).where(
                    TenantUsageDaily.tenant_id == tenant_id,
                    TenantUsageDaily.day == day,
                )
            )
        if row is None:
            return 0
        return int(row.tokens_in or 0) + int(row.tokens_out or 0)

    async def metrics_snapshot(self) -> dict:
        async with self._sessions() as session:
            failed_inbound = await session.scalar(
                select(func.count())
                .select_from(InboundPayload)
                .where(InboundPayload.status == "failed")
            )
            failed_messages = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.processing_status == "failed")
            )
            processing = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.processing_status.in_(["received", "processing"]))
            )
            oldest_failed = await session.scalar(
                select(InboundPayload.created_at)
                .where(InboundPayload.status == "failed")
                .order_by(InboundPayload.created_at.asc())
                .limit(1)
            )
        return {
            "failed_inbound": int(failed_inbound or 0),
            "failed_messages": int(failed_messages or 0),
            "in_flight_messages": int(processing or 0),
            "oldest_failed_inbound_at": oldest_failed.isoformat() if oldest_failed else None,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    async def apply_retention(
        self,
        *,
        message_days: Optional[int] = None,
        payload_days: Optional[int] = None,
        audit_days: Optional[int] = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        msg_cut = now - timedelta(days=message_days or RETENTION_DAYS["messages"])
        payload_cut = now - timedelta(days=payload_days or RETENTION_DAYS["inbound_payloads"])
        audit_cut = now - timedelta(days=audit_days or RETENTION_DAYS["audit"])
        async with self._sessions() as session:
            msg_result = await session.execute(
                delete(Message).where(Message.created_at < msg_cut)
            )
            payload_result = await session.execute(
                delete(InboundPayload).where(InboundPayload.created_at < payload_cut)
            )
            audit_result = await session.execute(
                delete(AdminAuditLog).where(AdminAuditLog.created_at < audit_cut)
            )
            await session.commit()
        return {
            "messages_deleted": msg_result.rowcount or 0,
            "inbound_payloads_deleted": payload_result.rowcount or 0,
            "audit_deleted": audit_result.rowcount or 0,
            "cutoffs": {
                "messages": msg_cut.isoformat(),
                "inbound_payloads": payload_cut.isoformat(),
                "audit": audit_cut.isoformat(),
            },
        }

    async def export_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> Optional[dict]:
        """Authorized export of conversation transcript (no secrets)."""
        from fivecent.adapters.persistence.repos import SqlConversationRepo

        repo = SqlConversationRepo(self._sessions)
        return await repo.get_conversation(tenant_id, conversation_id)


async def commercial_context(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str,
    conversation_id: str,
) -> dict:
    """Lead / quote / hold snapshot for inbox detail (ED-7)."""
    from fivecent.services.plugin_yachting.persistence.models import Hold, Lead, Quote

    async with sessions() as session:
        lead = await session.scalar(
            select(Lead)
            .where(Lead.tenant_id == tenant_id, Lead.conversation_id == conversation_id)
            .order_by(Lead.created_at.desc())
            .limit(1)
        )
        quote = None
        hold = None
        if lead and lead.quote_id:
            quote = await session.get(Quote, lead.quote_id)
        if lead:
            hold = await session.scalar(
                select(Hold)
                .where(Hold.tenant_id == tenant_id, Hold.lead_id == lead.id)
                .order_by(Hold.created_at.desc())
                .limit(1)
            )
        if hold is None:
            hold = await session.scalar(
                select(Hold)
                .where(
                    Hold.tenant_id == tenant_id,
                    Hold.conversation_id == conversation_id,
                )
                .order_by(Hold.created_at.desc())
                .limit(1)
            )
        latest_quote = quote
        if latest_quote is None:
            latest_quote = await session.scalar(
                select(Quote)
                .where(
                    Quote.tenant_id == tenant_id,
                    Quote.conversation_id == conversation_id,
                )
                .order_by(Quote.created_at.desc())
                .limit(1)
            )
    return {
        "lead": None
        if lead is None
        else {
            "lead_id": lead.id,
            "customer_name": lead.customer_name,
            "email": lead.email,
            "state": lead.state,
            "boat_id": lead.boat_id,
            "start_date": lead.start_date.isoformat() if lead.start_date else None,
            "days": lead.days,
            "quote_id": lead.quote_id,
        },
        "quote": None
        if latest_quote is None
        else {
            "quote_id": latest_quote.id,
            "boat_id": latest_quote.boat_id,
            "total": str(latest_quote.total),
            "currency": latest_quote.currency,
            "start_date": latest_quote.start_date.isoformat()
            if latest_quote.start_date
            else None,
            "days": latest_quote.days,
        },
        "hold": None
        if hold is None
        else {
            "hold_id": hold.id,
            "boat_id": hold.boat_id,
            "expires_at": hold.expires_at.isoformat() if hold.expires_at else None,
            "start_date": hold.start_date.isoformat() if hold.start_date else None,
            "days": hold.days,
        },
    }
