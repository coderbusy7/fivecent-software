from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


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
