from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Protocol, Sequence

from fivecent.domain.events import Channel, InboundMessageEvent
from fivecent.domain.fleet import ActiveHold
from fivecent.domain.quoting import Boat, QuoteBreakdown
from fivecent.domain.tenancy import TenantContext


class LockBusy(Exception):
    """Another turn is already running for this conversation."""


class CachePort(Protocol):
    def lock(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> AbstractAsyncContextManager[None]: ...

    async def is_human_active(self, tenant_id: str, channel: str, user_channel_id: str) -> bool: ...

    async def set_human_active(
        self, tenant_id: str, channel: str, user_channel_id: str, ttl_seconds: int = 86400
    ) -> None: ...

    async def get_window(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> list[dict]: ...

    async def replace_window(
        self, tenant_id: str, channel: str, user_channel_id: str, turns: list[dict]
    ) -> None: ...

    async def remember_outbound(self, tenant_id: str, provider_message_id: str) -> None: ...

    async def was_sent_by_bot(self, tenant_id: str, provider_message_id: str) -> bool: ...

    async def incr(self, key: str) -> int: ...


class MessageQueue(Protocol):
    async def enqueue(self, event: InboundMessageEvent) -> None: ...


class ChannelSender(Protocol):
    async def send(
        self, tenant_id: str, channel: Channel, user_channel_id: str, text: str
    ) -> str: ...


class BrokerAlertPort(Protocol):
    async def notify_hold(self, tenant_id: str, payload: dict) -> None: ...


class TenantRepo(Protocol):
    async def get(self, tenant_id: str) -> Optional[TenantContext]: ...

    async def id_for_whatsapp_phone(self, phone_number_id: str) -> Optional[str]: ...

    async def whatsapp_routing_map(self) -> dict[str, str]: ...


class FleetRepo(Protocol):
    async def list_active(self, tenant_id: str) -> Sequence[Boat]: ...

    async def get(self, tenant_id: str, boat_id: str) -> Optional[Boat]: ...


class HoldRepo(Protocol):
    async def list_active(self, tenant_id: str, now: datetime) -> Sequence[ActiveHold]: ...

    async def create(
        self,
        *,
        tenant_id: str,
        boat_id: str,
        start_date: date,
        days: int,
        expires_at: datetime,
        lead_id: str,
    ) -> None: ...


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


class ConversationRepo(Protocol):
    async def claim_inbound(self, event: InboundMessageEvent, role: str) -> Optional[str]:
        """Insert inbound message. Returns conversation_id or None if duplicate."""

    async def recent_turns(
        self, tenant_id: str, channel: str, user_channel_id: str, limit: int = 15
    ) -> list[dict]: ...

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
    ) -> None: ...

    async def record_raw_inbound(
        self, *, tenant_id: Optional[str], channel: str, body: str
    ) -> None: ...


class LLMPort(Protocol):
    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str | dict = "auto",
    ) -> LLMResult: ...


class LLMResult:
    def __init__(
        self,
        content: Optional[str],
        tool_calls: list[LLMToolCall],
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


class LLMToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments


class Container:
    def __init__(
        self,
        *,
        cache: CachePort,
        queue: MessageQueue,
        sender: ChannelSender,
        alerts: BrokerAlertPort,
        tenants: TenantRepo,
        fleet: FleetRepo,
        holds: HoldRepo,
        quotes: QuoteRepo,
        leads: LeadRepo,
        conversations: ConversationRepo,
        llm: LLMPort,
        seed_tenant_id: str,
        whatsapp_phone_number_id: str,
    ) -> None:
        self.cache = cache
        self.queue = queue
        self.sender = sender
        self.alerts = alerts
        self.tenants = tenants
        self.fleet = fleet
        self.holds = holds
        self.quotes = quotes
        self.leads = leads
        self.conversations = conversations
        self.llm = llm
        self.seed_tenant_id = seed_tenant_id
        self.whatsapp_phone_number_id = whatsapp_phone_number_id
