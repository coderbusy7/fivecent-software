"""ED-6 / ED-7: inbox workflow, named roles, tenant boundaries."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from fivecent.api.app import create_app
from fivecent.domain.events import InboundMessageEvent


@pytest.fixture
def admin_client(container, settings):
    settings.admin_api_token = "ops-secret"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        yield client, {"Authorization": "Bearer ops-secret"}


@pytest.mark.asyncio
async def test_inbox_list_takeover_resume_and_tenant_isolation(
    container, admin_client
) -> None:
    client, headers = admin_client
    event = InboundMessageEvent(
        tenant_id="port-vauban",
        channel="whatsapp",
        user_channel_id="+33699990001",
        text="hello inbox",
        provider_message_id="inbox-msg-1",
        timestamp=datetime.now(timezone.utc),
    )
    claimed = await container.conversations.claim_inbound(event, "user")
    assert claimed is not None

    listed = client.get(
        "/internal/tenants/port-vauban/conversations",
        headers=headers,
        params={"q": "33699990001"},
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] >= 1
    conversation_id = body["items"][0]["conversation_id"]

    detail = client.get(
        f"/internal/tenants/port-vauban/conversations/{conversation_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["human_active"] is False
    assert "commercial" in detail.json()

    takeover = client.post(
        f"/internal/tenants/port-vauban/conversations/{conversation_id}/takeover",
        headers=headers,
    )
    assert takeover.status_code == 200
    assert takeover.json()["handoff_status"] == "taken_over"
    assert takeover.json().get("bot_muted_until")
    assert await container.cache.is_human_active(
        "port-vauban", "whatsapp", "+33699990001"
    )
    assert await container.conversations.is_bot_muted(conversation_id)

    resume = client.post(
        f"/internal/tenants/port-vauban/conversations/{conversation_id}/resume-bot",
        headers=headers,
    )
    assert resume.status_code == 200
    assert resume.json()["handoff_status"] == "open"
    assert resume.json().get("bot_muted_until") is None
    assert not await container.cache.is_human_active(
        "port-vauban", "whatsapp", "+33699990001"
    )
    assert not await container.conversations.is_bot_muted(conversation_id)

    denied = client.get(
        "/internal/tenants/other-agency/conversations",
        headers=headers,
    )
    # platform admin can call, but tenant missing → 404
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_named_user_token_scoped_and_audit(container, admin_client) -> None:
    client, headers = admin_client
    created = client.post(
        "/internal/users",
        headers=headers,
        json={
            "email": "broker@agency.test",
            "display_name": "Agency Broker",
            "role": "broker",
            "tenant_id": "port-vauban",
        },
    )
    assert created.status_code == 200, created.text
    token = created.json()["api_token"]
    user_headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/internal/me", headers=user_headers)
    assert me.status_code == 200
    assert me.json()["role"] == "broker"
    assert me.json()["tenant_id"] == "port-vauban"

    ok = client.get("/internal/tenants/port-vauban", headers=user_headers)
    assert ok.status_code == 200

    # Broker cannot mint users or cross tenants.
    escalate = client.post(
        "/internal/users",
        headers=user_headers,
        json={
            "email": "evil@test",
            "display_name": "Evil",
            "role": "platform_admin",
        },
    )
    assert escalate.status_code == 403

    listed = client.get("/internal/tenants", headers=user_headers)
    assert listed.status_code == 200
    ids = {row["tenant_id"] for row in listed.json()["items"]}
    assert ids == {"port-vauban"}

    audit = client.get("/internal/audit", headers=headers)
    assert audit.status_code == 200
    actions = {row["action"] for row in audit.json()}
    assert "create" in actions


@pytest.mark.asyncio
async def test_disabled_user_token_revoked(container, admin_client) -> None:
    client, headers = admin_client
    created = client.post(
        "/internal/users",
        headers=headers,
        json={
            "email": "temp@agency.test",
            "display_name": "Temp",
            "role": "agency_admin",
            "tenant_id": "port-vauban",
        },
    )
    assert created.status_code == 200
    user_id = created.json()["user_id"]
    token = created.json()["api_token"]
    user_headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/internal/me", headers=user_headers).status_code == 200

    disabled = client.post(f"/internal/users/{user_id}/disable", headers=headers)
    assert disabled.status_code == 200
    assert client.get("/internal/me", headers=user_headers).status_code == 401
