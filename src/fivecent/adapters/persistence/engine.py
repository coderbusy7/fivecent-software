from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from fivecent.adapters.persistence.models import Base
from fivecent.adapters.persistence.seed import seed_if_empty
from fivecent.config import Settings
from fivecent.services.plugin_yachting.persistence import models as _yachting_models  # noqa: F401


def async_database_url(database_url: str) -> str:
    """Accept Render-style postgresql:// URLs for the async engine."""
    if database_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def sync_database_url(database_url: str) -> str:
    """Alembic uses a sync driver. Render URLs are postgresql:// (asyncpg in-app)."""
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + database_url[len("postgresql+asyncpg://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return database_url.replace("sqlite+aiosqlite", "sqlite")


def postgres_ssl_connect_args(url: str) -> dict:
    if "+asyncpg" not in url and "+psycopg" not in url:
        return {}
    hostport = url.split("@")[-1].split("/")[0].split("?")[0]
    host = hostport.rsplit(":", 1)[0]
    if (
        "ssl=" not in url
        and "sslmode=" not in url
        and "." in host
        and host not in {"localhost", "127.0.0.1"}
    ):
        if "+asyncpg" in url:
            return {"ssl": True}
        return {"sslmode": "require"}
    return {}


def make_engine(database_url: str) -> AsyncEngine:
    url = async_database_url(database_url)
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    ssl_args = postgres_ssl_connect_args(url)
    if ssl_args:
        kwargs["connect_args"] = ssl_args
    return create_async_engine(url, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_database(engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    if not settings.database_url.startswith("sqlite"):
        from fivecent.adapters.persistence.migrate import ensure_migrations

        ensure_migrations(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_if_empty(sessions, settings)
