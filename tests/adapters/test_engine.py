from fivecent.adapters.persistence.engine import async_database_url, sync_database_url


def test_render_postgres_url_uses_asyncpg() -> None:
    assert async_database_url("postgresql://u:p@dpg-host/db").startswith("postgresql+asyncpg://")
    assert async_database_url("postgres://u:p@dpg-host/db").startswith("postgresql+asyncpg://")
    assert async_database_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert async_database_url("sqlite+aiosqlite:///./x.db") == "sqlite+aiosqlite:///./x.db"


def test_alembic_postgres_url_uses_psycopg() -> None:
    assert sync_database_url("postgresql://u:p@dpg-host/db").startswith("postgresql+psycopg://")
    assert sync_database_url("postgresql+asyncpg://u:p@h/db").startswith("postgresql+psycopg://")
    assert sync_database_url("sqlite+aiosqlite:///./x.db") == "sqlite:///./x.db"
