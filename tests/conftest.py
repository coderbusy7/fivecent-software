from collections.abc import AsyncIterator

import pytest

from uuid import uuid4

from fivecent.adapters.cache.memory import MemoryCache
from fivecent.adapters.llm.openai_chat import ScriptedLLM
from fivecent.adapters.persistence.engine import init_database, make_engine, make_sessionmaker
from fivecent.adapters.queue.inline import InlineQueue
from fivecent.composition import AppContainer, build_container
from fivecent.config import Settings
from fivecent.domain.events import Channel


class CaptureSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(
        self, tenant_id: str, channel: Channel, user_channel_id: str, text: str
    ) -> str:
        self.sent.append((channel, user_channel_id, text))
        return f"out-{uuid4()}"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="",
        openai_api_key="",
        whatsapp_app_secret="test-app-secret",
        whatsapp_verify_token="verify-me",
        whatsapp_access_token="",
        whatsapp_phone_number_id="7794189252778687",
        seed_tenant_id="port-vauban",
        admin_api_token="",
    )


@pytest.fixture
async def container(settings: Settings) -> AsyncIterator[AppContainer]:
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    await init_database(engine, sessions, settings)
    llm = ScriptedLLM()
    built = await build_container(
        settings,
        engine=engine,
        sessions=sessions,
        llm=llm,
        cache=MemoryCache(),
        queue=InlineQueue(),
        sender=CaptureSender(),
    )
    yield built
    await built.aclose()
    await engine.dispose()
