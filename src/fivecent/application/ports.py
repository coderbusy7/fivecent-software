from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Optional, Protocol, Sequence

from fivecent.application.registry import PluginRegistry
from fivecent.application.tools import ServiceModule, ToolCall, ToolSpec
from fivecent.domain.events import Channel, InboundMessageEvent
from fivecent.domain.tenancy import TenantContext

ChatRole = Literal["system", "user", "assistant", "tool"]
ToolChoice = Literal["auto", "none"]


@dataclass(frozen=True)
class ChatMessage:
    role: ChatRole
    content: Optional[str] = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: Optional[str] = None


@dataclass(frozen=True)
class LLMResult:
    content: Optional[str]
    tool_calls: list[ToolCall]
    tokens_in: int
    tokens_out: int


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

    async def clear_human_active(
        self, tenant_id: str, channel: str, user_channel_id: str
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

    async def append_debounce_fragment(
        self,
        tenant_id: str,
        channel: str,
        user_channel_id: str,
        *,
        text: str,
        provider_message_id: str,
    ) -> None: ...

    async def bump_debounce_generation(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> int: ...

    async def get_debounce_generation(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> int: ...

    async def drain_debounce_fragments(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> list[dict]: ...


class MessageQueue(Protocol):
    async def enqueue(self, event: InboundMessageEvent) -> None: ...


class ChannelSender(Protocol):
    async def send(
        self, tenant_id: str, channel: Channel, user_channel_id: str, text: str
    ) -> str: ...


class OpsAlertPort(Protocol):
    """Kernel-facing broker push (Telegram today). Used for ops handoffs like 131047."""

    async def notify_handoff(self, tenant_id: str, payload: dict) -> bool: ...


class TenantRepo(Protocol):
    async def get(self, tenant_id: str) -> Optional[TenantContext]: ...

    async def channel_routing_map(self, channel: str) -> dict[str, str]: ...


class ConversationRepo(Protocol):
    async def claim_inbound(self, event: InboundMessageEvent, role: str) -> Optional[str]:
        """Claim inbound for processing. None means a terminal duplicate."""

    async def mark_inbound(
        self,
        tenant_id: str,
        provider_message_id: str,
        status: str,
        reason: Optional[str] = None,
    ) -> None: ...

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
    ) -> str: ...

    async def mark_raw_inbound(
        self, payload_id: str, *, status: str, error: Optional[str] = None
    ) -> None: ...

    async def get_raw_inbound(self, payload_id: str) -> Optional[dict]: ...

    async def list_failed_raw_inbound(self, *, limit: int = 50) -> list[dict]: ...


class LLMPort(Protocol):
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        *,
        model: str,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResult: ...


class Container:
    def __init__(
        self,
        *,
        cache: CachePort,
        queue: MessageQueue,
        sender: ChannelSender,
        tenants: TenantRepo,
        conversations: ConversationRepo,
        providers: PluginRegistry[LLMPort],
        services: PluginRegistry[ServiceModule],
        seed_tenant_id: str,
        debounce_seconds: float = 2.0,
        ops_alerts: Optional[OpsAlertPort] = None,
    ) -> None:
        self.cache = cache
        self.queue = queue
        self.sender = sender
        self.tenants = tenants
        self.conversations = conversations
        self.providers = providers
        self.services = services
        self.seed_tenant_id = seed_tenant_id
        self.debounce_seconds = debounce_seconds
        self.ops_alerts = ops_alerts
