from __future__ import annotations

from typing import Optional

from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fivecent.adapters.cache.memory import MemoryCache
from fivecent.adapters.cache.redis_store import RedisCache
from fivecent.adapters.llm.openai_chat import OpenAIChat, ScriptedLLM
from fivecent.adapters.outbound.whatsapp import CompositeSender, LogBrokerAlerts, WhatsAppSender
from fivecent.adapters.persistence.engine import make_engine, make_sessionmaker
from fivecent.adapters.persistence.repos import (
    SqlConversationRepo,
    SqlFleetRepo,
    SqlHoldRepo,
    SqlLeadRepo,
    SqlQuoteRepo,
    SqlTenantRepo,
)
from fivecent.adapters.queue.inline import InlineQueue
from fivecent.application.ports import CachePort, ChannelSender, Container, LLMPort, MessageQueue
from fivecent.config import Settings


class AppContainer(Container):
    def __init__(self, **kwargs) -> None:
        self._closers = kwargs.pop("closers", [])
        super().__init__(**kwargs)

    async def aclose(self) -> None:
        for closer in self._closers:
            try:
                await closer()
            except RuntimeError:
                pass


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
        if settings.redis_url and not for_worker:
            from arq.connections import RedisSettings, create_pool
            from fivecent.adapters.queue.arq_jobs import ArqQueue

            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            queue = ArqQueue(pool)
            closers.append(pool.aclose)
        else:
            queue = InlineQueue()

    if llm is None:
        if settings.openai_api_key:
            llm = OpenAIChat(
                AsyncOpenAI(api_key=settings.openai_api_key, timeout=30.0, max_retries=1),
                settings.openai_model,
            )
        else:
            llm = ScriptedLLM()

    if sender is None:
        whatsapp = None
        if settings.whatsapp_access_token:
            whatsapp = WhatsAppSender(
                access_token=settings.whatsapp_access_token,
                phone_number_id=settings.whatsapp_phone_number_id,
                graph_version=settings.whatsapp_graph_version,
            )
            closers.append(whatsapp.aclose)
        sender = CompositeSender(whatsapp)

    container = AppContainer(
        cache=cache,
        queue=queue,
        sender=sender,
        alerts=LogBrokerAlerts(),
        tenants=SqlTenantRepo(sessions),
        fleet=SqlFleetRepo(sessions),
        holds=SqlHoldRepo(sessions),
        quotes=SqlQuoteRepo(sessions),
        leads=SqlLeadRepo(sessions),
        conversations=SqlConversationRepo(sessions),
        llm=llm,
        seed_tenant_id=settings.seed_tenant_id,
        whatsapp_phone_number_id=settings.whatsapp_phone_number_id,
        closers=closers,
    )
    if isinstance(queue, InlineQueue):
        queue.container = container
    return container
