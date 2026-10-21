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
