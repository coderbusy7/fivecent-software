from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.adapters.persistence.models import Conversation, InboundPayload, Message
from fivecent.adapters.persistence.tenants import SqlTenantRepo
from fivecent.domain.events import InboundMessageEvent

__all__ = ["SqlTenantRepo", "SqlConversationRepo"]

# Incomplete inbound can be retried; terminal states are true duplicates.
_RETRYABLE_STATUSES = frozenset({"received", "processing", "failed"})
_TERMINAL_STATUSES = frozenset({"completed", "skipped"})
DEFAULT_HITL_MUTE_SECONDS = 24 * 60 * 60


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
                handoff_status="open",
                updated_at=datetime.now(timezone.utc),
            )
            session.add(conv)
            await session.flush()
        else:
            conv.updated_at = datetime.now(timezone.utc)
        return conv

    async def claim_inbound(self, event: InboundMessageEvent, role: str) -> Optional[str]:
        """Insert inbound as received, or reopen a retryable prior claim.

        Returns conversation_id when the turn should run. Returns None when the
        provider message was already completed or intentionally skipped.
        """
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
                    processing_status="received",
                    created_at=event.timestamp,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.execute(
                    select(Message).where(
                        Message.tenant_id == event.tenant_id,
                        Message.provider_message_id == event.provider_message_id,
                    )
                )
                row = existing.scalar_one_or_none()
                if row is None:
                    return None
                status = (row.processing_status or "completed").lower()
                if status in _TERMINAL_STATUSES:
                    return None
                if status in _RETRYABLE_STATUSES:
                    row.processing_status = "processing"
                    row.skip_reason = None
                    await session.commit()
                    return row.conversation_id
                return None
            return conv.id

    async def mark_inbound(
        self,
        tenant_id: str,
        provider_message_id: str,
        status: str,
        reason: Optional[str] = None,
    ) -> None:
        async with self._sessions() as session:
            result = await session.execute(
                select(Message).where(
                    Message.tenant_id == tenant_id,
                    Message.provider_message_id == provider_message_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.processing_status = status
            row.skip_reason = reason
            await session.commit()

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
                    processing_status="completed",
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    async def record_raw_inbound(
        self, *, tenant_id: Optional[str], channel: str, body: str
    ) -> str:
        payload_id = str(uuid4())
        async with self._sessions() as session:
            session.add(
                InboundPayload(
                    id=payload_id,
                    tenant_id=tenant_id,
                    channel=channel,
                    body=body,
                    status="accepted",
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        return payload_id

    async def mark_raw_inbound(
        self, payload_id: str, *, status: str, error: Optional[str] = None
    ) -> None:
        async with self._sessions() as session:
            row = await session.get(InboundPayload, payload_id)
            if row is None:
                return
            row.status = status
            row.error = error
            await session.commit()

    async def get_raw_inbound(self, payload_id: str) -> Optional[dict]:
        async with self._sessions() as session:
            row = await session.get(InboundPayload, payload_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "channel": row.channel,
                "body": row.body,
                "status": row.status,
                "error": row.error,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }

    async def list_failed_raw_inbound(self, *, limit: int = 50) -> list[dict]:
        async with self._sessions() as session:
            result = await session.execute(
                select(InboundPayload)
                .where(InboundPayload.status == "failed")
                .order_by(InboundPayload.created_at.desc())
                .limit(limit)
            )
            rows = list(result.scalars().all())
        return [
            {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "channel": row.channel,
                "status": row.status,
                "error": row.error,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    async def list_conversations(
        self,
        tenant_id: str,
        *,
        q: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        async with self._sessions() as session:
            stmt = select(Conversation).where(Conversation.tenant_id == tenant_id)
            if q.strip():
                like = f"%{q.strip()}%"
                stmt = stmt.where(
                    or_(
                        Conversation.user_channel_id.ilike(like),
                        Conversation.channel.ilike(like),
                        Conversation.id.ilike(like),
                    )
                )
            total = await session.scalar(
                select(func.count()).select_from(stmt.subquery())
            )
            rows = (
                await session.execute(
                    stmt.order_by(Conversation.updated_at.desc().nullslast())
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars().all()
            items = []
            for conv in rows:
                last = await session.scalar(
                    select(Message)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                failed = await session.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(
                        Message.conversation_id == conv.id,
                        Message.processing_status == "failed",
                    )
                )
                muted_until = _as_utc(conv.bot_muted_until)
                items.append(
                    {
                        "conversation_id": conv.id,
                        "tenant_id": conv.tenant_id,
                        "channel": conv.channel,
                        "user_channel_id": conv.user_channel_id,
                        "assigned_broker_id": conv.assigned_broker_id,
                        "handoff_status": conv.handoff_status or "open",
                        "bot_muted_until": muted_until.isoformat() if muted_until else None,
                        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                        "last_message_at": last.created_at.isoformat()
                        if last and last.created_at
                        else None,
                        "last_role": last.role if last else None,
                        "last_status": last.processing_status if last else None,
                        "failed_count": int(failed or 0),
                    }
                )
        return {"total": int(total or 0), "offset": offset, "limit": limit, "items": items}

    async def get_conversation(self, tenant_id: str, conversation_id: str) -> Optional[dict]:
        async with self._sessions() as session:
            conv = await session.get(Conversation, conversation_id)
            if conv is None or conv.tenant_id != tenant_id:
                return None
            messages = (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.asc())
                    .limit(200)
                )
            ).scalars().all()
            muted_until = _as_utc(conv.bot_muted_until)
            return {
                "conversation_id": conv.id,
                "tenant_id": conv.tenant_id,
                "channel": conv.channel,
                "user_channel_id": conv.user_channel_id,
                "assigned_broker_id": conv.assigned_broker_id,
                "handoff_status": conv.handoff_status or "open",
                "bot_muted_until": muted_until.isoformat() if muted_until else None,
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                "messages": [
                    {
                        "id": row.id,
                        "role": row.role,
                        "text": row.text,
                        "provider_message_id": row.provider_message_id,
                        "processing_status": row.processing_status,
                        "skip_reason": row.skip_reason,
                        "tokens_in": row.tokens_in,
                        "tokens_out": row.tokens_out,
                        "latency_ms": row.latency_ms,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in messages
                ],
            }

    async def is_bot_muted(
        self, conversation_id: str, *, now: Optional[datetime] = None
    ) -> bool:
        """True when durable HITL mute is still in effect."""
        clock = _as_utc(now) or datetime.now(timezone.utc)
        async with self._sessions() as session:
            conv = await session.get(Conversation, conversation_id)
            if conv is None:
                return False
            muted_until = _as_utc(conv.bot_muted_until)
            return muted_until is not None and muted_until > clock

    async def set_hitl_mute(
        self,
        tenant_id: str,
        channel: str,
        user_channel_id: str,
        *,
        muted: bool,
        mute_seconds: int = DEFAULT_HITL_MUTE_SECONDS,
        handoff_status: Optional[str] = None,
    ) -> Optional[str]:
        """Persist mute/unmute for a channel peer. Returns conversation_id if found/created."""
        now = datetime.now(timezone.utc)
        async with self._sessions() as session:
            result = await session.execute(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.channel == channel,
                    Conversation.user_channel_id == user_channel_id,
                )
            )
            conv = result.scalar_one_or_none()
            if conv is None:
                if not muted:
                    return None
                conv = Conversation(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    channel=channel,
                    user_channel_id=user_channel_id,
                    handoff_status=handoff_status or "taken_over",
                    bot_muted_until=now + timedelta(seconds=max(1, mute_seconds)),
                    updated_at=now,
                )
                session.add(conv)
            else:
                if muted:
                    conv.bot_muted_until = now + timedelta(seconds=max(1, mute_seconds))
                    if handoff_status is not None:
                        conv.handoff_status = handoff_status
                    elif conv.handoff_status == "open":
                        conv.handoff_status = "taken_over"
                else:
                    conv.bot_muted_until = None
                    if handoff_status is not None:
                        conv.handoff_status = handoff_status
                conv.updated_at = now
            await session.commit()
            return conv.id

    async def update_conversation_ops(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        assigned_broker_id: Optional[str] = None,
        handoff_status: Optional[str] = None,
        clear_assignment: bool = False,
        bot_muted_until: Optional[datetime] = None,
        clear_mute: bool = False,
    ) -> Optional[dict]:
        async with self._sessions() as session:
            conv = await session.get(Conversation, conversation_id)
            if conv is None or conv.tenant_id != tenant_id:
                return None
            if clear_assignment:
                conv.assigned_broker_id = None
            elif assigned_broker_id is not None:
                conv.assigned_broker_id = assigned_broker_id
            if handoff_status is not None:
                conv.handoff_status = handoff_status
            if clear_mute:
                conv.bot_muted_until = None
            elif bot_muted_until is not None:
                conv.bot_muted_until = bot_muted_until
            conv.updated_at = datetime.now(timezone.utc)
            await session.commit()
        return await self.get_conversation(tenant_id, conversation_id)

    async def delete_conversation(self, tenant_id: str, conversation_id: str) -> bool:
        async with self._sessions() as session:
            conv = await session.get(Conversation, conversation_id)
            if conv is None or conv.tenant_id != tenant_id:
                return False
            messages = (
                await session.execute(
                    select(Message).where(Message.conversation_id == conversation_id)
                )
            ).scalars().all()
            for row in messages:
                await session.delete(row)
            await session.delete(conv)
            await session.commit()
        return True
