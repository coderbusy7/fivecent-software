"""P0 reliability: retryable inbound, honest webhook ack, tenant WhatsApp send, config validation."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from fivecent.api.app import create_app
from fivecent.application.turn import handle_turn
from fivecent.domain.events import InboundMessageEvent
from fivecent.domain.tenancy import TenantConfigError
from fivecent.messaging.plugin_whatsapp.outbound import WhatsAppSender


def _event(**kwargs) -> InboundMessageEvent:
    payload = {
        "tenant_id": "port-vauban",
        "channel": "test",
        "user_channel_id": "+33612345678",
        "provider_message_id": "wamid.retry.1",
        "text": "Need a charter quote",
        "timestamp": datetime.now(timezone.utc),
    }
    payload.update(kwargs)
    return InboundMessageEvent.model_validate(payload)


async def test_outbound_failure_is_retryable(container) -> None:
    class FailingThenOk:
        def __init__(self) -> None:
            self.calls = 0
            self.sent: list[tuple] = []

        async def send(self, tenant_id, channel, user_channel_id, text):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("simulated outbound outage")
            self.sent.append((tenant_id, channel, user_channel_id, text))
            return "out-retry-ok"

    sender = FailingThenOk()
    container.sender = sender

    with pytest.raises(httpx.ConnectError):
        await handle_turn(_event(), container)

    retry = await handle_turn(_event(), container)
    assert retry.skipped is False
    assert retry.reply
    assert sender.calls == 2
    assert len(sender.sent) == 1

    duplicate = await handle_turn(_event(text="Again"), container)
    assert duplicate.reason == "duplicate"


async def test_webhook_queue_outage_returns_500(container, settings) -> None:
    class UnavailableQueue:
        async def enqueue(self, event) -> None:
            raise RuntimeError("simulated queue outage")

    container.queue = UnavailableQueue()
    app = create_app(
        container=container,
        settings=settings,
        phone_map={settings.whatsapp_phone_number_id: settings.seed_tenant_id},
    )
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {
                                "phone_number_id": settings.whatsapp_phone_number_id
                            },
                            "messages": [
                                {
                                    "id": "wamid.queue.fail",
                                    "type": "text",
                                    "from": "336999",
                                    "text": {"body": "Charter please"},
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode()
    digest = hmac.new(
        settings.whatsapp_app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"X-Hub-Signature-256": f"sha256={digest}"},
        )
    assert response.status_code == 500
    failed = await container.conversations.list_failed_raw_inbound()
    assert any(row["status"] == "failed" for row in failed)


async def test_tenant_whatsapp_sender_uses_per_tenant_phone() -> None:
    urls: list[str] = []

    def capture(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"messages": [{"id": "wa-out"}]})

    async def resolve(tenant_id: str):
        return {
            "tenant-a": "phone-a",
            "tenant-b": "phone-b",
        }.get(tenant_id)

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as client:
        sender = WhatsAppSender(
            access_token="token",
            phone_number_id="global-phone",
            graph_version="v21.0",
            client=client,
            resolve_phone_number_id=resolve,
        )
        await sender.send("tenant-a", "336111", "hello a")
        await sender.send("tenant-b", "336222", "hello b")

    assert urls[0].endswith("/phone-a/messages")
    assert urls[1].endswith("/phone-b/messages")


async def test_invalid_tenant_config_rejected(container) -> None:
    with pytest.raises(TenantConfigError):
        await container.tenants.update_tenant("port-vauban", vat_rate=Decimal("-0.5"))
    with pytest.raises(TenantConfigError):
        await container.tenants.update_tenant(
            "port-vauban", max_discount_pct=Decimal("2")
        )
    with pytest.raises(TenantConfigError):
        await container.tenants.update_tenant("port-vauban", routing_mode="invalid")
    with pytest.raises(TenantConfigError):
        await container.tenants.update_tenant(
            "port-vauban",
            feature_flags={"after_hours_start": "bad", "enable_after_hours_only": True},
        )


def test_internal_api_rejects_invalid_rates(container, settings) -> None:
    settings.admin_api_token = "admin-secret"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        response = client.patch(
            "/internal/tenants/port-vauban",
            headers={"Authorization": "Bearer admin-secret"},
            json={"vat_rate": "-0.5", "routing_mode": "invalid"},
        )
    assert response.status_code == 422


def test_whatsapp_test_send_uses_sender(container, settings) -> None:
    from fivecent.messaging.plugin_whatsapp.outbound import normalize_whatsapp_recipient

    assert normalize_whatsapp_recipient("0033621611871") == "33621611871"
    assert normalize_whatsapp_recipient("+33 6 21 61 18 71") == "33621611871"

    class WaCapture:
        def __init__(self) -> None:
            self.sent: list[tuple] = []

        async def send(self, tenant_id, channel, user_channel_id, text):
            self.sent.append((tenant_id, channel, user_channel_id, text))
            return "wa-test-out"

    capture = WaCapture()
    container.sender = capture
    settings.admin_api_token = "admin-secret"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        response = client.post(
            "/internal/whatsapp/test-send",
            headers={"Authorization": "Bearer admin-secret"},
            json={
                "phone": "0033621611871",
                "message": "ping",
                "tenant_id": "port-vauban",
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["to"] == "33621611871"
    assert body["outbound_id"] == "wa-test-out"
    assert capture.sent == [("port-vauban", "whatsapp", "33621611871", "ping")]
