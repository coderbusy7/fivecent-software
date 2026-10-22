"""End-to-end flow for the central dispatch bot: onboard → alert card → mute → HITL."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from fivecent.adapters.alerts.telegram import TelegramBrokerAlerts, format_telegram_alert
from fivecent.api.app import create_app
from fivecent.application.turn import handle_turn
from fivecent.domain.events import InboundMessageEvent


@pytest.mark.asyncio
async def test_dispatch_bot_onboard_alert_mute_hitl(container, settings) -> None:
    settings.telegram_bot_token = "tg-token"
    settings.telegram_bot_username = "fivecent_test_bot"
    settings.telegram_webhook_secret = ""
    settings.admin_api_token = "admin-secret"
    app = create_app(container=container, settings=settings, phone_map={})

    with (
        patch(
            "fivecent.messaging.plugin_telegram.inbound.send_telegram_text",
            new_callable=AsyncMock,
            return_value=True,
        ) as reply,
        TestClient(app) as client,
    ):
        # 1) Broker single-use invite onboarding
        invite = client.post(
            "/internal/tenants/port-vauban/telegram-invite",
            headers={"Authorization": "Bearer admin-secret"},
        )
        assert invite.status_code == 200
        token = invite.json()["token"]
        start = client.post(
            "/telegram/webhook",
            json={
                "message": {
                    "text": f"/start {token}",
                    "chat": {"id": 11223344},
                }
            },
        )
        assert start.status_code == 200
        assert "Connected" in reply.await_args.kwargs["text"]

        tenant = client.get(
            "/internal/tenants/port-vauban",
            headers={"Authorization": "Bearer admin-secret"},
        )
        assert tenant.status_code == 200
        body = tenant.json()
        assert body["telegram_chat_id"] == "11223344"

    ctx = await container.tenants.get("port-vauban")
    assert ctx is not None
    assert ctx.telegram_chat_id == "11223344"

    # 2) Rich lead card routes to desk chat id
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request.read().decode())
        return httpx.Response(200, json={"ok": True})

    payload = {
        "agency_name": "Port Vauban",
        "customer_name": "Alexander Wright",
        "boat_name": "Sunseeker Predator 60",
        "start_date": "2026-07-18",
        "days": 1,
        "quote_total": "6120.00",
        "currency": "EUR",
        "wa_me": "https://wa.me/19175550192",
        "mute_command": "/mute_19175550192_port-vauban",
        "tenant_telegram_chat_id": ctx.telegram_chat_id,
        "alert_brokers": [{"name": "Pierre", "telegram_chat_id": "999"}],
    }
    card = format_telegram_alert("port-vauban", "hold", payload)
    assert "https://wa.me/19175550192" in card
    assert "/mute_19175550192_port-vauban" in card

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        alerts = TelegramBrokerAlerts(bot_token="tg-token", client=http)
        await alerts.deliver("port-vauban", "hold", payload)
    assert len(posts) == 1
    assert "11223344" in posts[0]
    assert "999" not in posts[0]  # desk preferred over broker fallback

    # 3) Broker mute → Elena silent on next WhatsApp turn
    with (
        patch(
            "fivecent.messaging.plugin_telegram.inbound.send_telegram_text",
            new_callable=AsyncMock,
            return_value=True,
        ),
        TestClient(app) as client,
    ):
        muted = client.post(
            "/telegram/webhook",
            json={
                "message": {
                    "text": "/mute_19175550192_port-vauban",
                    "chat": {"id": 11223344},
                }
            },
        )
        assert muted.status_code == 200

    assert await container.cache.is_human_active(
        "port-vauban", "whatsapp", "19175550192"
    )

    result = await handle_turn(
        InboundMessageEvent(
            tenant_id="port-vauban",
            channel="whatsapp",
            user_channel_id="19175550192",
            provider_message_id="wamid.mute-e2e",
            text="Still interested — can we lock it?",
            timestamp=datetime.now(timezone.utc),
        ),
        container,
    )
    assert result.skipped is True
    assert result.reason == "hitl"
