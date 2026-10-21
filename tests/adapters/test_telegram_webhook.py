from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from fivecent.api.app import create_app


def _app(container, settings, *, token: str = "tg-secret"):
    settings.telegram_bot_token = token
    settings.telegram_bot_username = "fivecent_dispatch_bot"
    settings.telegram_webhook_secret = "hook-secret"
    return create_app(container=container, settings=settings, phone_map={})


@pytest.mark.asyncio
async def test_start_links_tenant_desk_chat_id(container, settings) -> None:
    app = _app(container, settings)
    with (
        patch(
            "fivecent.messaging.plugin_telegram.inbound.send_telegram_text",
            new_callable=AsyncMock,
            return_value=True,
        ) as reply,
        TestClient(app) as client,
    ):
        response = client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
            json={
                "message": {
                    "text": "/start port-vauban",
                    "chat": {"id": 84729103},
                }
            },
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        reply.assert_awaited()
        text = reply.await_args.kwargs["text"]
        assert "Connected" in text or "connected" in text.lower()

    ctx = await container.tenants.get("port-vauban")
    assert ctx is not None
    assert ctx.telegram_chat_id == "84729103"


@pytest.mark.asyncio
async def test_start_rejects_unknown_tenant(container, settings) -> None:
    app = _app(container, settings)
    with (
        patch(
            "fivecent.messaging.plugin_telegram.inbound.send_telegram_text",
            new_callable=AsyncMock,
            return_value=True,
        ) as reply,
        TestClient(app) as client,
    ):
        response = client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
            json={
                "message": {
                    "text": "/start does-not-exist",
                    "chat": {"id": 1},
                }
            },
        )
        assert response.status_code == 200
        assert "Unknown" in reply.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_mute_sets_human_active_circuit_breaker(container, settings) -> None:
    app = _app(container, settings)
    with (
        patch(
            "fivecent.messaging.plugin_telegram.inbound.send_telegram_text",
            new_callable=AsyncMock,
            return_value=True,
        ) as reply,
        TestClient(app) as client,
    ):
        response = client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
            json={
                "message": {
                    "text": "/mute_19175550192_port-vauban",
                    "chat": {"id": 99},
                }
            },
        )
        assert response.status_code == 200
        assert "muted" in reply.await_args.kwargs["text"].lower()

    active = await container.cache.is_human_active(
        "port-vauban", "whatsapp", "19175550192"
    )
    assert active is True


def test_webhook_secret_required(container, settings) -> None:
    app = _app(container, settings)
    with TestClient(app) as client:
        denied = client.post(
            "/telegram/webhook",
            json={"message": {"text": "/start port-vauban", "chat": {"id": 1}}},
        )
        assert denied.status_code == 403


def test_patch_tenant_and_broker_telegram(container, settings) -> None:
    settings.admin_api_token = "admin-secret"
    settings.telegram_bot_username = "fivecent_dispatch_bot"
    app = create_app(container=container, settings=settings, phone_map={})
    headers = {"Authorization": "Bearer admin-secret"}
    with TestClient(app) as client:
        patched = client.patch(
            "/internal/tenants/port-vauban",
            headers=headers,
            json={"telegram_chat_id": "11223344"},
        )
        assert patched.status_code == 200, patched.text
        body = patched.json()
        assert body["telegram_chat_id"] == "11223344"
        assert body["telegram_invite_link"] == (
            "https://t.me/fivecent_dispatch_bot?start=port-vauban"
        )

        broker = client.post(
            "/internal/tenants/port-vauban/brokers",
            headers=headers,
            json={
                "name": "Pierre",
                "phone_number": "+33600000001",
                "telegram_chat_id": "999",
            },
        )
        assert broker.status_code == 200
        broker_id = broker.json()["broker_id"]
        updated = client.patch(
            f"/internal/brokers/{broker_id}",
            headers=headers,
            json={"telegram_chat_id": "888"},
        )
        assert updated.status_code == 200
        assert updated.json()["telegram_chat_id"] == "888"

        cleared = client.patch(
            "/internal/tenants/port-vauban",
            headers=headers,
            json={"telegram_chat_id": ""},
        )
        assert cleared.status_code == 200
        assert cleared.json()["telegram_chat_id"] is None
