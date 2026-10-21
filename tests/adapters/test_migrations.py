from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from fivecent.adapters.persistence.migrate import PLUGIN_COLUMNS, ensure_migrations


def test_tenant_plugin_migration_adds_service_and_model_selection(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database = tmp_path / "migration.db"
    alembic_config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")

    command.upgrade(alembic_config, "0002_tenant_config")
    with sqlite3.connect(database) as connection:
        for name in ("service_type", "llm_provider", "llm_model"):
            connection.execute(f"ALTER TABLE tenants DROP COLUMN {name}")
    command.upgrade(alembic_config, "head")

    engine = create_engine(f"sqlite:///{database}")
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("tenants")}
    finally:
        engine.dispose()
    assert {"service_type", "llm_provider", "llm_model"} <= columns


def test_ensure_migrations_upgrades_legacy_create_all_database(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "legacy.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE tenants (
                    id VARCHAR(64) PRIMARY KEY,
                    agency_name VARCHAR(128),
                    home_port VARCHAR(64),
                    currency VARCHAR(8),
                    whatsapp_phone_number_id VARCHAR(64),
                    whatsapp_waba_id VARCHAR(64),
                    instagram_account_id VARCHAR(64),
                    messenger_page_id VARCHAR(64),
                    default_vat_rate NUMERIC(4, 2),
                    default_apa_rate NUMERIC(4, 2),
                    max_discount_pct NUMERIC(4, 2),
                    routing_mode VARCHAR(32),
                    feature_flags JSON,
                    is_active BOOLEAN,
                    created_at DATETIME
                )
                """
            )
        )
    engine.dispose()

    ensure_migrations(url)

    engine = create_engine(url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("tenants")}
    finally:
        engine.dispose()
    assert PLUGIN_COLUMNS <= columns
