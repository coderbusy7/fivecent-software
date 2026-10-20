from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from fivecent.adapters.persistence.models import Base
from fivecent.adapters.persistence.seed import seed_if_empty
from fivecent.config import Settings


def async_database_url(database_url: str) -> str:
    """Accept Render-style postgresql:// URLs for the async engine."""
    if database_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def make_engine(database_url: str) -> AsyncEngine:
    url = async_database_url(database_url)
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    elif "+asyncpg" in url:
        hostport = url.split("@")[-1].split("/")[0].split("?")[0]
        host = hostport.rsplit(":", 1)[0]
        # Public hosts (e.g. *.render.com) need TLS; Render's internal
        # hostname (dpg-…-a) does not.
        if (
            "ssl=" not in url
            and "sslmode=disable" not in url
            and "." in host
            and host not in {"localhost", "127.0.0.1"}
        ):
            kwargs["connect_args"] = {"ssl": True}
    return create_async_engine(url, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_database(engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_if_empty(sessions, settings)
