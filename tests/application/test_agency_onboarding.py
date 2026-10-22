"""ED-10: agency onboarding → fleet → quote/hold path without shared inventory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fivecent.api.app import create_app
from fivecent.application.tools import ToolCall, ToolContext
from fivecent.domain.events import InboundMessageEvent


@pytest.fixture
def admin_client(container, settings):
    settings.admin_api_token = "onboard-secret"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        yield client, {"Authorization": "Bearer onboard-secret"}


@pytest.mark.asyncio
async def test_fresh_agency_gets_namespaced_fleet_and_setup_status(
    container, admin_client, settings
) -> None:
    client, headers = admin_client
    created = client.post(
        "/internal/tenants",
        headers=headers,
        json={
            "tenant_id": "cannes-lux",
            "agency_name": "Cannes Lux",
            "home_port": "Cannes",
            "whatsapp_phone_number_id": "phone-cannes-lux",
            "whatsapp_waba_id": "waba-cannes",
            "service_type": "yachting",
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
        },
    )
    assert created.status_code == 200, created.text

    fleet = client.get("/internal/tenants/cannes-lux/fleet", headers=headers)
    assert fleet.status_code == 200
    boats = fleet.json()
    assert len(boats) >= 2
    ids = {boat["boat_id"] for boat in boats}
    assert "cannes-lux__lagoon-450" in ids
    assert "lagoon-450" not in ids  # must not steal seed tenant PK

    seed_fleet = await container.services.get("yachting")._fleet.list_active("port-vauban")
    seed_ids = {boat.id for boat in seed_fleet}
    assert "lagoon-450" in seed_ids
    assert not (ids & seed_ids)

    status = client.get("/internal/tenants/cannes-lux/setup-status", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["status"] in {"draft", "misconfigured", "ready"}
    assert body["checks"]["has_active_boat"] is True
    assert isinstance(body["checks"]["whatsapp_access_token_present"], bool)
    # Presence flag only — never leak a real secret value.
    secret = (settings.whatsapp_access_token or "").strip()
    if secret:
        assert secret not in json.dumps(body)


@pytest.mark.asyncio
async def test_fleet_validation_and_deactivate(admin_client) -> None:
    client, headers = admin_client
    client.post(
        "/internal/tenants",
        headers=headers,
        json={
            "tenant_id": "nice-yachts",
            "agency_name": "Nice Yachts",
            "home_port": "Nice",
            "whatsapp_phone_number_id": "phone-nice-yachts",
            "service_type": "yachting",
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
        },
    )
    bad = client.post(
        "/internal/tenants/nice-yachts/fleet",
        headers=headers,
        json={
            "boat_id": "BAD ID",
            "name": "X",
            "length_m": "12",
            "capacity": 14,
            "low_season_daily_rate": "100",
            "high_season_daily_rate": "120",
            "skipper_daily_rate": "50",
        },
    )
    assert bad.status_code == 422

    ok = client.post(
        "/internal/tenants/nice-yachts/fleet",
        headers=headers,
        json={
            "boat_id": "pearl-60",
            "name": "Pearl 60",
            "length_m": "18.5",
            "capacity": 10,
            "low_season_daily_rate": "4000",
            "high_season_daily_rate": "5200",
            "skipper_daily_rate": "450",
            "active": True,
        },
    )
    assert ok.status_code == 200, ok.text
    boat_id = ok.json()["boat_id"]
    assert boat_id == "nice-yachts__pearl-60"

    deactivated = client.patch(
        f"/internal/tenants/nice-yachts/fleet/{boat_id}",
        headers=headers,
        json={"active": False},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False


@pytest.mark.asyncio
async def test_onboarding_availability_quote_hold_flow(container, admin_client) -> None:
    client, headers = admin_client
    client.post(
        "/internal/tenants",
        headers=headers,
        json={
            "tenant_id": "antibes-desk",
            "agency_name": "Antibes Desk",
            "home_port": "Port Vauban",
            "whatsapp_phone_number_id": "phone-antibes-desk",
            "service_type": "yachting",
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
            "feature_flags": {"enable_soft_hold": True, "enable_instant_quote": True},
        },
    )
    # Ensure desk routing looks configured for setup-status.
    client.patch(
        "/internal/tenants/antibes-desk",
        headers=headers,
        json={"telegram_chat_id": "desk-antibes-1"},
    )

    tenant = await container.tenants.get("antibes-desk")
    assert tenant is not None
    prepared = await container.services.get("yachting").prepare(tenant)
    boat_ids = [boat.id for boat in await container.services.get("yachting")._fleet.list_active(tenant.id)]
    assert boat_ids
    boat_id = boat_ids[0]

    conv = await container.conversations.claim_inbound(
        InboundMessageEvent.model_validate(
            {
                "tenant_id": "antibes-desk",
                "channel": "test",
                "user_channel_id": "+33620000099",
                "provider_message_id": "wamid.onboard.1",
                "text": "charter",
                "timestamp": datetime.now(timezone.utc),
            }
        ),
        "user",
    )
    avail = json.loads(
        await prepared.tools.execute(
            ToolCall(
                "a1",
                "check_fleet_availability",
                json.dumps({"pax": 8, "start_date": "2028-08-01", "days": 2}),
            ),
            ToolContext(tenant, conv),
        )
    )
    assert avail.get("ok") is True or "boats" in avail

    quote_raw = await prepared.tools.execute(
        ToolCall(
            "a2",
            "calculate_itemized_quote",
            json.dumps(
                {
                    "boat_id": boat_id,
                    "start_date": "2028-08-01",
                    "days": 2,
                    "skipper": True,
                }
            ),
        ),
        ToolContext(tenant, conv),
    )
    quote = json.loads(quote_raw)
    assert quote.get("ok") is True
    assert Decimal(str(quote.get("total") or 0)) > 0

    hold = json.loads(
        await prepared.tools.execute(
            ToolCall(
                "a3",
                "escalate_and_soft_hold",
                json.dumps(
                    {
                        "customer_name": "Alex Onboard",
                        "email": "alex.onboard@example.com",
                        "boat_id": boat_id,
                        "start_date": "2028-08-01",
                        "days": 2,
                        "quote_id": quote.get("quote_id"),
                    }
                ),
            ),
            ToolContext(tenant, conv, channel="whatsapp", user_channel_id="+33620000099"),
        )
    )
    assert hold.get("ok") is True, hold

    follow = json.loads(
        await prepared.tools.execute(
            ToolCall(
                "a4",
                "request_broker_followup",
                json.dumps(
                    {
                        "destination": "Monaco",
                        "contact_preference": "whatsapp",
                        "customer_name": "Alex Onboard",
                    }
                ),
            ),
            ToolContext(tenant, conv, channel="whatsapp", user_channel_id="+33620000099"),
        )
    )
    assert follow.get("ok") is True
