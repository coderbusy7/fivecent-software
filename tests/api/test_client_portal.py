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
