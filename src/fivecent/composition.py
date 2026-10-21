from __future__ import annotations

from typing import Optional

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fivecent.adapters.cache.memory import MemoryCache
from fivecent.adapters.cache.redis_store import RedisCache
from fivecent.adapters.llm.anthropic_chat import AnthropicChat
from fivecent.adapters.llm.openai_chat import OpenAIChat
from fivecent.adapters.outbound.whatsapp import (
    ChannelSenderRegistry,
    LogBrokerAlerts,
    OutboundChannel,
    TestChannelSender,
    WhatsAppSender,
)
from fivecent.adapters.persistence.engine import make_engine, make_sessionmaker
from fivecent.adapters.persistence.repos import SqlConversationRepo, SqlTenantRepo
from fivecent.adapters.queue.inline import InlineQueue
from fivecent.application.ports import CachePort, ChannelSender, Container, LLMPort, MessageQueue
from fivecent.application.registry import PluginRegistry
from fivecent.config import Settings
from fivecent.services.yachting import YachtingService
from fivecent.services.yachting.persistence import (
    SqlFleetRepo,
    SqlHoldRepo,
    SqlLeadRepo,
    SqlQuoteRepo,
)


class AppContainer(Container):
    def __init__(self, **kwargs) -> None:
        self._closers = kwargs.pop("closers", [])
        super().__init__(**kwargs)

    async def aclose(self) -> None:
        for closer in self._closers:
            await closer()


async def build_container(
    settings: Settings,
    *,
    engine: Optional[AsyncEngine] = None,
    sessions: Optional[async_sessionmaker[AsyncSession]] = None,
    llm: Optional[LLMPort] = None,
    cache: Optional[CachePort] = None,
    queue: Optional[MessageQueue] = None,
    sender: Optional[ChannelSender] = None,
    for_worker: bool = False,
) -> AppContainer:
    owns_engine = engine is None
    if engine is None:
        engine = make_engine(settings.database_url)
    if sessions is None:
        sessions = make_sessionmaker(engine)

    closers = []
    if owns_engine:
        closers.append(engine.dispose)

    redis_client: Optional[Redis] = None
    if cache is None:
        if settings.redis_url:
            redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
            cache = RedisCache(redis_client)
            closers.append(redis_client.aclose)
        else:
            cache = MemoryCache()

    if queue is None:
        if settings.redis_url and settings.queue_backend == "arq" and not for_worker:
            from arq.connections import RedisSettings, create_pool
            from fivecent.adapters.queue.arq_jobs import ArqQueue

            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            queue = ArqQueue(pool)
            closers.append(pool.aclose)
        else:
            queue = InlineQueue()

    providers: dict[str, LLMPort] = {}
    if llm is not None:
        providers["openai"] = llm
    elif settings.openai_api_key:
        openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key, timeout=30.0, max_retries=1
        )
        providers["openai"] = OpenAIChat(openai_client)
        closers.append(openai_client.close)
    if settings.anthropic_api_key:
        anthropic_client = AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=30.0, max_retries=1
        )
        providers["anthropic"] = AnthropicChat(anthropic_client)
        closers.append(anthropic_client.close)

    if sender is None:
        channels: dict[str, OutboundChannel] = {"test": TestChannelSender()}
        whatsapp = None
        if settings.whatsapp_access_token:
            whatsapp = WhatsAppSender(
                access_token=settings.whatsapp_access_token,
                phone_number_id=settings.whatsapp_phone_number_id,
                graph_version=settings.whatsapp_graph_version,
            )
            closers.append(whatsapp.aclose)
            channels["whatsapp"] = whatsapp
        sender = ChannelSenderRegistry(channels)

    fleet_repo = SqlFleetRepo(sessions)
    hold_repo = SqlHoldRepo(sessions)
    quote_repo = SqlQuoteRepo(sessions)
    lead_repo = SqlLeadRepo(sessions)
    alert_port = LogBrokerAlerts()
    container = AppContainer(
        cache=cache,
        queue=queue,
        sender=sender,
        tenants=SqlTenantRepo(sessions),
        conversations=SqlConversationRepo(sessions),
        providers=PluginRegistry(providers),
        services=PluginRegistry({
            "yachting": YachtingService(
                fleet=fleet_repo,
                holds=hold_repo,
                quotes=quote_repo,
                leads=lead_repo,
                cache=cache,
                alerts=alert_port,
            ),
        }),
        seed_tenant_id=settings.seed_tenant_id,
        closers=closers,
    )
    if isinstance(queue, InlineQueue):
        queue.container = container
    return container
