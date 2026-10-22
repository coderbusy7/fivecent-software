"""ED-8: production config validation and readiness probes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fivecent.api.app import create_app
from fivecent.config import Settings
from fivecent.production import ProductionConfigError, validate_production_settings


def test_production_rejects_sqlite_and_inline() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="",
        queue_backend="inline",
        admin_api_token="x",
        whatsapp_verify_token="v",
        whatsapp_app_secret="s",
        whatsapp_access_token="t",
        whatsapp_phone_number_id="seed-phone-number-id",
    )
    with pytest.raises(ProductionConfigError) as exc:
        validate_production_settings(settings)
    message = str(exc.value)
    assert "Postgres" in message
    assert "REDIS_URL" in message
    assert "arq" in message


def test_production_accepts_safe_settings() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        queue_backend="arq",
        admin_api_token="token",
        whatsapp_verify_token="v",
        whatsapp_app_secret="s",
        whatsapp_access_token="t",
        whatsapp_phone_number_id="7794189252778687",
        telegram_bot_token="",
        telegram_webhook_secret="",
        enable_test_chat=False,
    )
    validate_production_settings(settings)


def test_live_and_ready_endpoints(container, settings) -> None:
    settings.app_env = "development"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        assert client.get("/live").status_code == 200
        ready = client.get("/ready")
        assert ready.status_code == 200
        body = ready.json()
        assert body["ready"] is True
        assert body["checks"]["database"]["ok"] is True
