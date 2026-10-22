from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.adapters.persistence.models import Conversation, InboundPayload, Message
from fivecent.adapters.persistence.tenants import SqlTenantRepo
from fivecent.domain.events import InboundMessageEvent

__all__ = ["SqlTenantRepo", "SqlConversationRepo"]

# Incomplete inbound can be retried; terminal states are true duplicates.
_RETRYABLE_STATUSES = frozenset({"received", "processing", "failed"})
_TERMINAL_STATUSES = frozenset({"completed", "skipped"})


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
