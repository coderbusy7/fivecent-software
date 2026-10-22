"""Live Postgres/Redis fixtures. Skipped unless INTEGRATION_* env vars are set."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from fivecent.adapters.cache.redis_store import RedisCache
from fivecent.adapters.llm.openai_chat import ScriptedLLM
from fivecent.adapters.persistence.engine import init_database, make_engine, make_sessionmaker
from fivecent.adapters.queue.inline import InlineQueue
from fivecent.composition import AppContainer, build_container
from fivecent.config import Settings
from uuid import uuid4

from fivecent.domain.events import Channel


def _integration_database_url() -> str | None:
    return os.environ.get("INTEGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL_INTEGRATION")


def _integration_redis_url() -> str | None:
    return os.environ.get("INTEGRATION_REDIS_URL") or os.environ.get("REDIS_URL_INTEGRATION")


pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    db = _integration_database_url()
    redis = _integration_redis_url()
    if not db or "sqlite" in db.lower():
        pytest.skip("INTEGRATION_DATABASE_URL (Postgres) is required")
    if not redis:
        pytest.skip("INTEGRATION_REDIS_URL is required")
    return Settings(
        _env_file=None,
        database_url=db,
        redis_url=redis,
        openai_api_key="",
        whatsapp_app_secret="test-app-secret",
        whatsapp_verify_token="verify-me",
        whatsapp_access_token="",
        whatsapp_phone_number_id="7794189252778687",
        seed_tenant_id="port-vauban",
        admin_api_token="integration-admin",
        debounce_seconds=0.0,
        queue_backend="inline",
        app_env="test",
    )


@pytest.fixture
async def integration_container(
    integration_settings: Settings,
) -> AsyncIterator[AppContainer]:
    engine = make_engine(integration_settings.database_url)
    sessions = make_sessionmaker(engine)
    await init_database(engine, sessions, integration_settings)
    redis = Redis.from_url(integration_settings.redis_url, decode_responses=True)
    cache = RedisCache(redis)

    class CaptureSender:
        def __init__(self) -> None:
            self.sent: list[tuple] = []

        async def send(self, tenant_id, channel: Channel, user_channel_id, text):
            self.sent.append((tenant_id, channel, user_channel_id, text))
            return f"out-{uuid4()}"

    built = await build_container(
        integration_settings,
        engine=engine,
        sessions=sessions,
        llm=ScriptedLLM(),
        cache=cache,
        queue=InlineQueue(),
        sender=CaptureSender(),
    )
    yield built
    await built.aclose()
    await redis.aclose()
    await engine.dispose()


@pytest.fixture
async def redis_cache(integration_settings: Settings) -> AsyncIterator[RedisCache]:
    client = Redis.from_url(integration_settings.redis_url, decode_responses=True)
    await client.flushdb()
    cache = RedisCache(client)
    yield cache
    await client.aclose()
