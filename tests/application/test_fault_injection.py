"""Fault-injection coverage index for ED-11 (queue/send/alert recovery).

Detailed cases live in reliability/soft-hold modules; this module keeps the
acceptance matrix explicit and runnable in the default suite.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from fivecent.api.app import create_app
from fivecent.application.tools import ToolCall, ToolContext
from fivecent.application.turn import handle_turn
from fivecent.domain.events import InboundMessageEvent


def _event(**kwargs) -> InboundMessageEvent:
    payload = {
        "tenant_id": "port-vauban",
        "channel": "test",
        "user_channel_id": "+33612345678",
        "provider_message_id": "wamid.fault.1",
        "text": "Need a charter quote",
        "timestamp": datetime.now(timezone.utc),
    }
    payload.update(kwargs)
    return InboundMessageEvent.model_validate(payload)


@pytest.mark.asyncio
async def test_queue_outage_surfaces_on_webhook(container, settings) -> None:
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
                                    "from": "33612345678",
                                    "id": "wamid.fault.queue",
                                    "timestamp": "1710000000",
                                    "type": "text",
                                    "text": {"body": "hi"},
                                }
                            ],
                            "contacts": [{"wa_id": "33612345678"}],
                        },
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode()
    import hashlib
    import hmac

    sig = hmac.new(
        settings.whatsapp_app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": f"sha256={sig}",
            },
        )
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_send_failure_then_recovery(container) -> None:
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        async def send(self, tenant_id, channel, user_channel_id, text):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("outage")
            return "out-ok"

    container.sender = Flaky()
    with pytest.raises(httpx.ConnectError):
        await handle_turn(_event(provider_message_id="wamid.fault.send"), container)
    retry = await handle_turn(_event(provider_message_id="wamid.fault.send"), container)
    assert retry.skipped is False or retry.reason is None
    assert container.sender.calls == 2


@pytest.mark.asyncio
async def test_alert_replay_after_channel_failure(container) -> None:
    class FlakyAlerts:
        def __init__(self) -> None:
            self.calls = 0

        async def notify_hold(self, tenant_id: str, payload: dict) -> bool:
            self.calls += 1
            return self.calls > 1

        async def notify_handoff(self, tenant_id: str, payload: dict) -> bool:
            return True

        async def notify_followup(self, tenant_id: str, payload: dict) -> bool:
            return True

    alerts = FlakyAlerts()
    yachting = container.services.get("yachting")
    yachting._alerts = alerts  # type: ignore[attr-defined]
    yachting._tools._alerts = alerts  # type: ignore[attr-defined]
    tenant = await container.tenants.get("port-vauban")
    prepared = await yachting.prepare(tenant)
    conv = await container.conversations.claim_inbound(
        _event(provider_message_id="wamid.fault.alert", user_channel_id="+33615555555"),
        "user",
    )
    raw = await prepared.tools.execute(
        ToolCall(
            "call_hold",
            "escalate_and_soft_hold",
            json.dumps(
                {
                    "customer_name": "Ada",
                    "email": "ada@example.com",
                    "boat_id": "lagoon-450",
                    "start_date": "2029-01-15",
                    "days": 1,
                }
            ),
        ),
        ToolContext(tenant, conv, channel="whatsapp"),
    )
    assert json.loads(raw)["ok"] is True
    flushed = await yachting.flush_broker_alerts()
    assert flushed["delivered"] >= 1
    assert alerts.calls >= 2
