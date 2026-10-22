"""ED-11: live Postgres/Redis integration coverage."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from fivecent.adapters.persistence.engine import sync_database_url
from fivecent.adapters.persistence.migrate import ensure_migrations
from fivecent.application.ports import LockBusy
from fivecent.application.tools import ToolCall, ToolContext
from fivecent.domain.events import InboundMessageEvent
from fivecent.services.plugin_yachting.persistence.models import Hold, Lead
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def _event(**kwargs) -> InboundMessageEvent:
    payload = {
        "tenant_id": "port-vauban",
        "channel": "test",
        "user_channel_id": "+33612345678",
        "provider_message_id": "wamid.integration.1",
        "text": "hello",
        "timestamp": datetime.now(timezone.utc),
    }
    payload.update(kwargs)
    return InboundMessageEvent.model_validate(payload)


@pytest.mark.asyncio
async def test_migrations_apply_on_postgres(integration_settings) -> None:
    ensure_migrations(integration_settings.database_url)
    # sync ping after migrate
    from sqlalchemy import create_engine

    engine = create_engine(sync_database_url(integration_settings.database_url))
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar()
            assert row == 1
            tables = {
                name
                for (name,) in conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                    )
                )
            }
            assert "tenants" in tables
            assert "holds" in tables
            assert "broker_alert_outbox" in tables
            assert "telegram_desk_invites" in tables
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_ingestion_is_terminal(integration_container) -> None:
    event = _event(provider_message_id="wamid.dup.1", text="one")
    first = await integration_container.conversations.claim_inbound(event, "user")
    assert first is not None
    await integration_container.conversations.mark_inbound(
        event.tenant_id, event.provider_message_id, "completed"
    )
    second = await integration_container.conversations.claim_inbound(
        _event(provider_message_id="wamid.dup.1", text="two"), "user"
    )
    assert second is None


@pytest.mark.asyncio
async def test_redis_lock_rejects_concurrent_holder(redis_cache) -> None:
    async with redis_cache.lock("port-vauban", "whatsapp", "336"):
        with pytest.raises(LockBusy):
            async with redis_cache.lock("port-vauban", "whatsapp", "336"):
                pass


@pytest.mark.asyncio
async def test_concurrent_overlapping_holds_single_winner(integration_container) -> None:
    tenant = await integration_container.tenants.get("port-vauban")
    prepared = await integration_container.services.get("yachting").prepare(tenant)
    args = json.dumps(
        {
            "customer_name": "Ada",
            "email": "ada@example.com",
            "boat_id": "lagoon-450",
            "start_date": "2028-06-01",
            "days": 2,
        }
    )
    c1 = await integration_container.conversations.claim_inbound(
        _event(provider_message_id="wamid.hold.pg.1", user_channel_id="+33610000001"),
        "user",
    )
    c2 = await integration_container.conversations.claim_inbound(
        _event(provider_message_id="wamid.hold.pg.2", user_channel_id="+33610000002"),
        "user",
    )

    async def place(conversation_id: str, call_id: str) -> dict:
        raw = await prepared.tools.execute(
            ToolCall(call_id, "escalate_and_soft_hold", args),
            ToolContext(tenant, conversation_id, channel="whatsapp"),
        )
        return json.loads(raw)

    first, second = await asyncio.gather(
        place(c1, "call_a"),
        place(c2, "call_b"),
    )
    outcomes = {first.get("ok"), second.get("ok")}
    assert True in outcomes
    assert False in outcomes or "hold_conflict" in {
        first.get("code"),
        second.get("code"),
    }

    sessions = integration_container.tenants._sessions  # type: ignore[attr-defined]
    async with sessions() as session:
        hold_count = await session.scalar(
            select(func.count())
            .select_from(Hold)
            .where(Hold.expires_at > datetime.now(timezone.utc))
        )
        lead_count = await session.scalar(select(func.count()).select_from(Lead))
    assert hold_count == 1
    assert lead_count == 1


@pytest.mark.asyncio
async def test_tenant_separation_on_routing_map(integration_container) -> None:
    repo = integration_container.tenants
    other = await repo.create_tenant(
        tenant_id="agency-integration-b",
        agency_name="Agency B",
        home_port="Cannes",
        whatsapp_phone_number_id="phone-agency-b",
        whatsapp_waba_id="",
        currency="EUR",
        vat_rate=__import__("decimal").Decimal("0.20"),
        apa_rate=__import__("decimal").Decimal("0.30"),
        max_discount_pct=__import__("decimal").Decimal("0.08"),
        service_type="yachting",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        routing_mode="broadcast",
        feature_flags=None,
    )
    assert other.id == "agency-integration-b"
    routing = await repo.channel_routing_map("whatsapp")
    assert routing.get("phone-agency-b") == "agency-integration-b"
    assert routing.get("7794189252778687") == "port-vauban" or "port-vauban" in routing.values()
