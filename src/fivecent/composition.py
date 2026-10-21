from __future__ import annotations

from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fivecent.adapters.cache.memory import MemoryCache
from fivecent.adapters.cache.redis_store import RedisCache
from fivecent.adapters.llm.factory import build_llm_plugins
from fivecent.adapters.persistence.engine import make_engine, make_sessionmaker
from fivecent.adapters.persistence.repos import SqlConversationRepo, SqlTenantRepo
from fivecent.adapters.queue.inline import InlineQueue
from fivecent.application.ports import CachePort, ChannelSender, Container, LLMPort, MessageQueue
from fivecent.application.registry import PluginRegistry
from fivecent.config import Settings
from fivecent.messaging.factory import build_outbound_plugins
from fivecent.services.factory import build_service_plugins


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

    providers = build_llm_plugins(settings, closers, llm=llm)
    if sender is None:
        sender = build_outbound_plugins(settings, closers)

    container = AppContainer(
        cache=cache,
        queue=queue,
        sender=sender,
        tenants=SqlTenantRepo(sessions),
        conversations=SqlConversationRepo(sessions),
        providers=PluginRegistry(providers),
        services=build_service_plugins(sessions, cache),
        seed_tenant_id=settings.seed_tenant_id,
        closers=closers,
    )
    if isinstance(queue, InlineQueue):
        queue.container = container
    return container
