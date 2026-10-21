from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from fivecent.api.app import create_app
from fivecent.application.tools import ToolCall, ToolContext
from fivecent.domain.events import InboundMessageEvent
from fivecent.services.plugin_yachting.domain.quoting import calculate_itemized_quote
from tests.services.yachting.test_quoting import LAGOON


async def test_soft_hold_flag_disables_tool(container) -> None:
    tenant = await container.tenants.get("port-vauban")
    assert tenant is not None
    await container.tenants.update_tenant(
        "port-vauban", feature_flags={"enable_soft_hold": False}
    )
    tenant = await container.tenants.get("port-vauban")
    conv = await container.conversations.claim_inbound(
        InboundMessageEvent(
            tenant_id="port-vauban",
            channel="test",
            user_channel_id="+336999",
            provider_message_id="wamid.flag",
            text="hold",
            timestamp=datetime.now(timezone.utc),
        ),
        "user",
    )
    prepared = await container.services.get("yachting").prepare(tenant)
    raw = await prepared.tools.execute(
        ToolCall(
            id="call_hold",
            name="escalate_and_soft_hold",
            arguments=(
                '{"customer_name":"Ada","email":"ada@example.com",'
                '"boat_id":"lagoon-450","start_date":"2026-06-06"}'
            ),
        ),
        ToolContext(tenant=tenant, conversation_id=conv),
    )
    assert "feature_disabled" in raw


async def test_five_percent_floor_uses_tenant_rule() -> None:
    quote = calculate_itemized_quote(LAGOON, date(2026, 6, 6), 2)
    from fivecent.services.plugin_yachting.domain.negotiation import evaluate_negotiation_floor

    result = evaluate_negotiation_floor(quote, Decimal("6000"), max_discount_pct=Decimal("0.05"))
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "below_floor"


def test_internal_admin_creates_tenant(container, settings) -> None:
    settings.admin_api_token = "admin-secret"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        denied = client.post("/internal/tenants", json={"tenant_id": "x"})
        assert denied.status_code == 401
        created = client.post(
            "/internal/tenants",
            headers={"Authorization": "Bearer admin-secret"},
            json={
                "tenant_id": "cannes_luxury",
                "agency_name": "Cannes Luxury Marine",
                "home_port": "Cannes Vieux Port",
                "whatsapp_phone_number_id": "phone-cannes-1",
                "whatsapp_waba_id": "waba-cannes",
                "apa_rate": "0.25",
                "max_discount_pct": "0.05",
                "service_type": "yachting",
                "llm_provider": "openai",
                "llm_model": "gpt-4o-mini",
                "feature_flags": {"enable_after_hours_only": True},
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["agency_name"] == "Cannes Luxury Marine"
        assert body["apa_rate"] == "0.25"
        assert body["service_type"] == "yachting"
        assert body["llm_provider"] == "openai"
        assert body["llm_model"] == "gpt-4o-mini"
        unsupported_provider = client.patch(
            "/internal/tenants/cannes_luxury",
            headers={"Authorization": f"Bearer {settings.admin_api_token}"},
            json={"llm_provider": "not-configured"},
        )
        assert unsupported_provider.status_code == 422
        broker = client.post(
            "/internal/tenants/cannes_luxury/brokers",
            headers={"Authorization": "Bearer admin-secret"},
            json={"name": "Jules", "phone_number": "+33611111111"},
        )
        assert broker.status_code == 200
        fetched = client.get(
            "/internal/tenants/cannes_luxury",
            headers={"Authorization": "Bearer admin-secret"},
        )
        assert len(fetched.json()["brokers"]) == 1
        listed = client.get("/internal/tenants", headers={"Authorization": "Bearer admin-secret"})
        assert listed.status_code == 200
        ids = {row["tenant_id"] for row in listed.json()}
        assert "cannes_luxury" in ids
        broker_id = broker.json()["broker_id"]
        paused = client.patch(
            f"/internal/brokers/{broker_id}",
            headers={"Authorization": "Bearer admin-secret"},
            json={"is_active": False},
        )
        assert paused.status_code == 200
