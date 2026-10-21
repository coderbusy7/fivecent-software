from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from fivecent.adapters.persistence.engine import postgres_ssl_connect_args, sync_database_url

PLUGIN_COLUMNS = {"service_type", "llm_provider", "llm_model"}


def _alembic_config() -> Config:
    ini = Path.cwd() / "alembic.ini"
    if not ini.exists():
        raise RuntimeError("alembic.ini not found; run from the repository root.")
    return Config(str(ini))


def ensure_migrations(database_url: str) -> None:
    """Apply Alembic on an existing Render DB that may have been created with create_all."""
    sync_url = sync_database_url(database_url)
    cfg = _alembic_config()
    engine = create_engine(sync_url, connect_args=postgres_ssl_connect_args(sync_url))
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            if "alembic_version" not in tables and "tenants" in tables:
                columns = {column["name"] for column in inspector.get_columns("tenants")}
                if PLUGIN_COLUMNS <= columns:
                    command.stamp(cfg, "head")
                    return
                command.stamp(cfg, "0002_tenant_config")
    finally:
        engine.dispose()
    command.upgrade(cfg, "head")


def main() -> None:
    import os

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must be set.")
    ensure_migrations(url)


if __name__ == "__main__":
    main()
