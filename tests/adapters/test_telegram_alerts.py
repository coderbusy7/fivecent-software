from __future__ import annotations

import httpx
import pytest

from fivecent.adapters.alerts.telegram import (
    TelegramBrokerAlerts,
    format_telegram_alert,
    telegram_chat_ids,
)
from fivecent.services.plugin_yachting.alerts import CompositeBrokerAlerts, LogBrokerAlerts


def test_format_hold_includes_wa_me() -> None:
    text = format_telegram_alert(
        "port-vauban",
        "hold",
        {
            "boat_id": "lagoon-450",
            "start_date": "2026-06-06",
            "days": 2,
            "expires_at": "2026-06-07T12:00:00+00:00",
            "wa_me": "https://wa.me/33612345678",
        },
    )
    assert "24h option" in text
    assert "lagoon-450" in text
    assert "https://wa.me/33612345678" in text


def test_format_followup_includes_destination() -> None:
    text = format_telegram_alert(
        "port-vauban",
        "followup",
        {
            "destination": "Monaco",
            "contact_preference": "whatsapp",
            "customer_name": "Ada",
            "wa_me": "https://wa.me/336999",
        },
    )
    assert "Monaco" in text
    assert "Ada" in text
    assert "https://wa.me/336999" in text


def test_telegram_chat_ids_skips_empty() -> None:
    assert telegram_chat_ids({
        "alert_brokers": [
            {"name": "A", "telegram_chat_id": "111"},
            {"name": "B", "telegram_chat_id": ""},
            {"name": "C", "telegram_chat_id": None},
            {"name": "D"},
        ]
    }) == ["111"]


@pytest.mark.asyncio
async def test_telegram_sends_to_each_chat_id() -> None:
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request.read().decode())
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        alerts = TelegramBrokerAlerts(bot_token="secret-token", client=client)
        await alerts.deliver(
            "port-vauban",
            "hold",
            {
                "boat_id": "lagoon-450",
                "start_date": "2026-06-06",
                "days": 1,
                "wa_me": "https://wa.me/33612345678",
                "alert_brokers": [
                    {"name": "Elena", "telegram_chat_id": "100"},
                    {"name": "Marc", "telegram_chat_id": "200"},
                ],
            },
        )

    assert len(posts) == 2
    assert '"chat_id": "100"' in posts[0] or '"chat_id":"100"' in posts[0]
    assert "https://wa.me/33612345678" in posts[0]
    assert "api.telegram.org/botsecret-token/sendMessage" not in posts[0]


@pytest.mark.asyncio
async def test_telegram_skips_when_no_chat_ids() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        alerts = TelegramBrokerAlerts(bot_token="secret-token", client=client)
        await alerts.deliver("port-vauban", "hold", {"alert_brokers": []})

    assert called is False


@pytest.mark.asyncio
async def test_composite_continues_after_channel_failure() -> None:
    delivered: list[tuple[str, str]] = []

    class Boom:
        async def deliver(self, tenant_id: str, kind: str, payload: dict) -> None:
            raise RuntimeError("boom")

    class Ok:
        async def deliver(self, tenant_id: str, kind: str, payload: dict) -> None:
            delivered.append((tenant_id, kind))

    composite = CompositeBrokerAlerts([Boom(), Ok(), LogBrokerAlerts()])
    await composite.notify_hold("port-vauban", {"ok": True})
    assert delivered == [("port-vauban", "hold")]
