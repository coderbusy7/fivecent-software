"""ED-15: company vs client portal auth, isolation, and analytics."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from fivecent.api.app import create_app
from fivecent.domain.events import InboundMessageEvent


@pytest.fixture
def admin_client(container, settings):
    settings.admin_api_token = "portal-secret"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        yield client, {"Authorization": "Bearer portal-secret"}


@pytest.mark.asyncio
async def test_client_invite_set_password_login_and_analytics(
    container, admin_client
) -> None:
    client, headers = admin_client
    event = InboundMessageEvent(
        tenant_id="port-vauban",
        channel="whatsapp",
        user_channel_id="+33670001111",
        text="portal inquiry",
        provider_message_id="portal-inq-1",
        timestamp=datetime.now(timezone.utc),
    )
    assert await container.conversations.claim_inbound(event, "user")

    invited = client.post(
        "/internal/portal/invite",
        headers=headers,
        json={
            "email": "broker.client@agency.test",
            "display_name": "Agency Viewer",
            "tenant_id": "port-vauban",
        },
    )
    assert invited.status_code == 200, invited.text
    invite_token = invited.json()["invite_token"]

    activated = client.post(
        "/internal/portal/set-password",
        json={"invite_token": invite_token, "password": "client-portal-pass"},
    )
    assert activated.status_code == 200, activated.text
    api_token = activated.json()["api_token"]
    portal_headers = {"Authorization": f"Bearer {api_token}"}

    me = client.get("/internal/me", headers=portal_headers)
    assert me.status_code == 200
    assert me.json()["role"] == "client_user"
    assert me.json()["workspace"] == "client_portal"
    assert me.json()["tenant_id"] == "port-vauban"

    overview = client.get("/internal/portal/overview", headers=portal_headers)
    assert overview.status_code == 200
    assert overview.json()["tenant_id"] == "port-vauban"
    assert "agency_name" in overview.json()

    analytics = client.get("/internal/portal/analytics", headers=portal_headers)
    assert analytics.status_code == 200
    body = analytics.json()
    assert body["metrics"]["inquiries"] >= 1
    assert "not confirmed bookings" in body["disclaimer"].lower() or "not confirmed" in body[
        "disclaimer"
    ].lower()

    # Client cannot hit company admin / config surfaces.
    denied = client.get("/internal/tenants", headers=portal_headers)
    assert denied.status_code == 403
    denied_patch = client.patch(
        "/internal/tenants/port-vauban",
        headers=portal_headers,
        json={"agency_name": "Hacked"},
    )
    assert denied_patch.status_code == 403
    denied_metrics = client.get("/internal/metrics", headers=portal_headers)
    assert denied_metrics.status_code == 403


@pytest.mark.asyncio
async def test_revoked_client_cannot_login_or_use_token(
    container, admin_client
) -> None:
    client, headers = admin_client
    invited = client.post(
        "/internal/portal/invite",
        headers=headers,
        json={
            "email": "temp.client@agency.test",
            "display_name": "Temp",
            "tenant_id": "port-vauban",
        },
    )
    assert invited.status_code == 200
    user_id = invited.json()["user_id"]
    invite_token = invited.json()["invite_token"]
    activated = client.post(
        "/internal/portal/set-password",
        json={"invite_token": invite_token, "password": "temp-client-pass"},
    )
    assert activated.status_code == 200
    token = activated.json()["api_token"]
    portal_headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/internal/portal/overview", headers=portal_headers).status_code == 200

    revoked = client.post(f"/internal/users/{user_id}/disable", headers=headers)
    assert revoked.status_code == 200

    assert client.get("/internal/portal/overview", headers=portal_headers).status_code == 401
    login = client.post(
        "/internal/portal/login",
        json={"email": "temp.client@agency.test", "password": "temp-client-pass"},
    )
    assert login.status_code == 401


@pytest.mark.asyncio
async def test_client_cannot_access_other_tenant_analytics(
    container, admin_client
) -> None:
    client, headers = admin_client
    # Second agency
    created = client.post(
        "/internal/tenants",
        headers=headers,
        json={
            "tenant_id": "other-agency",
            "agency_name": "Other",
            "home_port": "Nice",
            "whatsapp_phone_number_id": "phone-other-portal",
            "whatsapp_waba_id": "waba-other",
        },
    )
    assert created.status_code == 200, created.text

    invited = client.post(
        "/internal/portal/invite",
        headers=headers,
        json={
            "email": "viewer@other.test",
            "display_name": "Other Viewer",
            "tenant_id": "other-agency",
        },
    )
    assert invited.status_code == 200
    activated = client.post(
        "/internal/portal/set-password",
        json={
            "invite_token": invited.json()["invite_token"],
            "password": "other-client-pass",
        },
    )
    token = activated.json()["api_token"]
    portal_headers = {"Authorization": f"Bearer {token}"}

    overview = client.get("/internal/portal/overview", headers=portal_headers)
    assert overview.status_code == 200
    assert overview.json()["tenant_id"] == "other-agency"

    # Portal analytics are always scoped to the authenticated tenant — no tenant_id param.
    analytics = client.get(
        "/internal/portal/analytics",
        headers=portal_headers,
        params={"date_from": "2020-01-01"},
    )
    assert analytics.status_code == 200
    assert analytics.json()["tenant_id"] == "other-agency"

    # Direct company tenant fetch for another agency is forbidden.
    cross = client.get("/internal/tenants/port-vauban", headers=portal_headers)
    assert cross.status_code == 403


@pytest.mark.asyncio
async def test_provision_client_skips_invite_token(admin_client) -> None:
    client, headers = admin_client
    provisioned = client.post(
        "/internal/portal/provision",
        headers=headers,
        json={
            "email": "ready@agency.test",
            "display_name": "Ready",
            "tenant_id": "port-vauban",
            "password": "ready-client-pass",
        },
    )
    assert provisioned.status_code == 200, provisioned.text
    login = client.post(
        "/internal/portal/login",
        json={"email": "ready@agency.test", "password": "ready-client-pass"},
    )
    assert login.status_code == 200
    assert login.json()["role"] == "client_user"
    overview = client.get(
        "/internal/portal/overview",
        headers={"Authorization": f"Bearer {login.json()['api_token']}"},
    )
    assert overview.status_code == 200


@pytest.mark.asyncio
async def test_analytics_date_filter_and_empty_range(admin_client) -> None:
    client, headers = admin_client
    invited = client.post(
        "/internal/portal/invite",
        headers=headers,
        json={
            "email": "empty@agency.test",
            "display_name": "Empty",
            "tenant_id": "port-vauban",
        },
    )
    activated = client.post(
        "/internal/portal/set-password",
        json={
            "invite_token": invited.json()["invite_token"],
            "password": "empty-client-pass",
        },
    )
    portal_headers = {"Authorization": f"Bearer {activated.json()['api_token']}"}
    analytics = client.get(
        "/internal/portal/analytics",
        headers=portal_headers,
        params={"date_from": "2099-01-01", "date_to": "2099-01-31"},
    )
    assert analytics.status_code == 200
    metrics = analytics.json()["metrics"]
    assert metrics["inquiries"] == 0
    assert metrics["leads"] == 0
    assert metrics["quotes"] == 0


@pytest.mark.asyncio
async def test_agency_ops_sees_contact_and_can_toggle_fleet(
    container, admin_client
) -> None:
    """ED-17: client_user redacts PII and cannot mutate; agency_ops can."""
    from datetime import date, timedelta
    from uuid import uuid4

    from fivecent.services.plugin_yachting.persistence.models import Hold, Lead

    client, headers = admin_client
    conversation_id = await container.conversations.claim_inbound(
        InboundMessageEvent(
            tenant_id="port-vauban",
            channel="whatsapp",
            user_channel_id="+33670002222",
            text="hold seed",
            provider_message_id="portal-hold-seed",
            timestamp=datetime.now(timezone.utc),
        ),
        "user",
    )
    assert conversation_id

    sessions = container.conversations._sessions
    async with sessions() as session:
        lead = Lead(
            id=str(uuid4()),
            tenant_id="port-vauban",
            conversation_id=conversation_id,
            customer_name="Guest Skipper",
            email="guest@example.com",
            boat_id="lagoon-450",
            start_date=date.today() + timedelta(days=7),
            days=3,
            quote_id=None,
            state="option",
            created_at=datetime.now(timezone.utc),
        )
        session.add(lead)
        session.add(
            Hold(
                id=str(uuid4()),
                tenant_id="port-vauban",
                boat_id="lagoon-450",
                start_date=date.today() + timedelta(days=7),
                days=3,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=20),
                lead_id=lead.id,
                created_at=datetime.now(timezone.utc),
                conversation_id=conversation_id,
            )
        )
        await session.commit()

    viewer = client.post(
        "/internal/portal/provision",
        headers=headers,
        json={
            "email": "viewer.ed17@agency.test",
            "display_name": "Viewer",
            "tenant_id": "port-vauban",
            "password": "viewer-ed17-pass",
            "role": "client_user",
        },
    )
    assert viewer.status_code == 200, viewer.text
    ops = client.post(
        "/internal/portal/provision",
        headers=headers,
        json={
            "email": "ops.ed17@agency.test",
            "display_name": "Ops",
            "tenant_id": "port-vauban",
            "password": "ops-ed17-passxx",
            "role": "agency_ops",
        },
    )
    assert ops.status_code == 200, ops.text

    viewer_login = client.post(
        "/internal/portal/login",
        json={"email": "viewer.ed17@agency.test", "password": "viewer-ed17-pass"},
    )
    assert viewer_login.status_code == 200
    viewer_h = {"Authorization": f"Bearer {viewer_login.json()['api_token']}"}

    ops_login = client.post(
        "/internal/portal/login",
        json={"email": "ops.ed17@agency.test", "password": "ops-ed17-passxx"},
    )
    assert ops_login.status_code == 200
    ops_h = {"Authorization": f"Bearer {ops_login.json()['api_token']}"}

    me = client.get("/internal/me", headers=ops_h)
    assert me.status_code == 200
    assert me.json()["role"] == "agency_ops"
    assert me.json()["capabilities"]["view_guest_contact"] is True
    assert me.json()["capabilities"]["mutate_fleet"] is True

    viewer_holds = client.get("/internal/portal/holds", headers=viewer_h)
    assert viewer_holds.status_code == 200
    assert viewer_holds.json()["include_contact"] is False
    assert all("contact" not in item for item in viewer_holds.json()["items"])

    ops_holds = client.get("/internal/portal/holds", headers=ops_h)
    assert ops_holds.status_code == 200
    assert ops_holds.json()["include_contact"] is True
    contacts = [item.get("contact") for item in ops_holds.json()["items"] if item.get("contact")]
    assert contacts and contacts[0]["email"] == "guest@example.com"

    fleet = client.get("/internal/portal/fleet", headers=viewer_h)
    assert fleet.status_code == 200
    assert fleet.json()["can_mutate"] is False
    boats = fleet.json()["items"]
    assert boats
    boat_id = boats[0]["boat_id"]

    denied = client.patch(
        f"/internal/portal/fleet/{boat_id}",
        headers=viewer_h,
        json={"active": False},
    )
    assert denied.status_code == 403

    toggled = client.patch(
        f"/internal/portal/fleet/{boat_id}",
        headers=ops_h,
        json={"active": False},
    )
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["active"] is False
    assert toggled.json()["in_maintenance"] is True

    restored = client.patch(
        f"/internal/portal/fleet/{boat_id}",
        headers=ops_h,
        json={"active": True},
    )
    assert restored.status_code == 200
    assert restored.json()["active"] is True
