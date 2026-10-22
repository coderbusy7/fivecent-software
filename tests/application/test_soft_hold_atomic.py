"""ED-3: atomic soft-hold reservation and durable broker alert recovery."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from fivecent.application.tools import ToolCall, ToolContext
from fivecent.domain.events import InboundMessageEvent
from fivecent.services.plugin_yachting.domain.quoting import QuoteBreakdown
from fivecent.services.plugin_yachting.persistence.models import BrokerAlertOutbox, Hold, Lead


def _event(**kwargs) -> InboundMessageEvent:
    payload = {
        "tenant_id": "port-vauban",
        "channel": "test",
        "user_channel_id": "+33612345678",
        "provider_message_id": "wamid.hold.atomic.1",
        "text": "Hold please",
        "timestamp": datetime.now(timezone.utc),
    }
    payload.update(kwargs)
    return InboundMessageEvent.model_validate(payload)


async def _hold_args(
    *,
    boat_id: str = "lagoon-450",
    start_date: str = "2027-07-06",
    days: int = 2,
    quote_id: str | None = None,
) -> str:
    body = {
        "customer_name": "Ada",
        "email": "ada@example.com",
        "boat_id": boat_id,
        "start_date": start_date,
        "days": days,
    }
    if quote_id is not None:
        body["quote_id"] = quote_id
    return json.dumps(body)


@pytest.mark.asyncio
async def test_hold_conflict_does_not_orphan_lead(container) -> None:
    tenant = await container.tenants.get("port-vauban")
    prepared = await container.services.get("yachting").prepare(tenant)
    arguments = await _hold_args()
    first_conversation = await container.conversations.claim_inbound(
        _event(provider_message_id="wamid.hold.atomic.a"), "user"
    )
    second_conversation = await container.conversations.claim_inbound(
        _event(
            provider_message_id="wamid.hold.atomic.b",
            user_channel_id="+33611111111",
        ),
        "user",
    )
    first = json.loads(
        await prepared.tools.execute(
            ToolCall("call_1", "escalate_and_soft_hold", arguments),
            ToolContext(tenant, first_conversation, channel="whatsapp"),
        )
    )
    second = json.loads(
        await prepared.tools.execute(
            ToolCall("call_2", "escalate_and_soft_hold", arguments),
            ToolContext(tenant, second_conversation, channel="whatsapp"),
        )
    )
    assert first["ok"] is True
    assert second["code"] == "hold_conflict"

    sessions = container.tenants._sessions  # type: ignore[attr-defined]
    async with sessions() as session:
        lead_count = await session.scalar(select(func.count()).select_from(Lead))
        hold_count = await session.scalar(
            select(func.count()).select_from(Hold).where(Hold.expires_at > datetime.now(timezone.utc))
        )
    assert lead_count == 1
    assert hold_count == 1


@pytest.mark.asyncio
async def test_soft_hold_replay_is_idempotent(container) -> None:
    tenant = await container.tenants.get("port-vauban")
    prepared = await container.services.get("yachting").prepare(tenant)
    arguments = await _hold_args(start_date="2027-08-01")
    conversation = await container.conversations.claim_inbound(
        _event(provider_message_id="wamid.hold.idem.1"), "user"
    )
    ctx = ToolContext(tenant, conversation, channel="whatsapp")
    first = json.loads(
        await prepared.tools.execute(
            ToolCall("call_a", "escalate_and_soft_hold", arguments), ctx
        )
    )
    second = json.loads(
        await prepared.tools.execute(
            ToolCall("call_b", "escalate_and_soft_hold", arguments), ctx
        )
    )
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["lead_id"] == second["lead_id"]
    assert first["hold_id"] == second["hold_id"]

    sessions = container.tenants._sessions  # type: ignore[attr-defined]
    async with sessions() as session:
        lead_count = await session.scalar(select(func.count()).select_from(Lead))
        hold_count = await session.scalar(select(func.count()).select_from(Hold))
        alert_count = await session.scalar(select(func.count()).select_from(BrokerAlertOutbox))
    assert lead_count == 1
    assert hold_count == 1
    assert alert_count == 1


@pytest.mark.asyncio
async def test_quote_mismatch_rejects_hold(container) -> None:
    tenant = await container.tenants.get("port-vauban")
    conversation = await container.conversations.claim_inbound(
        _event(provider_message_id="wamid.hold.quote.1"), "user"
    )
    yachting = container.services.get("yachting")
    quote_id = "quote-mismatch-1"
    await yachting._tools._quotes.save(  # type: ignore[attr-defined]
        tenant_id=tenant.id,
        conversation_id=conversation,
        quote_id=quote_id,
        quote=QuoteBreakdown(
            boat_id="lagoon-450",
            boat_name="Lagoon 450",
            start_date=date(2027, 9, 1),
            days=3,
            season="high",
            daily_rate=Decimal("2400"),
            skipper_included=True,
            base=Decimal("7200"),
            skipper=Decimal("0"),
            vat=Decimal("1440"),
            apa=Decimal("2160"),
            total=Decimal("10800"),
            currency="EUR",
            policy_version="test",
        ),
    )
    prepared = await yachting.prepare(tenant)
    # Same boat but different dates than the saved quote.
    raw = await prepared.tools.execute(
        ToolCall(
            "call_q",
            "escalate_and_soft_hold",
            await _hold_args(start_date="2027-09-10", days=3, quote_id=quote_id),
        ),
        ToolContext(tenant, conversation, channel="whatsapp"),
    )
    assert json.loads(raw)["code"] == "quote_mismatch"

    sessions = container.tenants._sessions  # type: ignore[attr-defined]
    async with sessions() as session:
        hold_count = await session.scalar(select(func.count()).select_from(Hold))
        lead_count = await session.scalar(select(func.count()).select_from(Lead))
    assert hold_count == 0
    assert lead_count == 0


@pytest.mark.asyncio
async def test_alert_failure_keeps_hold_and_recovers_on_flush(container) -> None:
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
    conversation = await container.conversations.claim_inbound(
        _event(provider_message_id="wamid.hold.alert.1"), "user"
    )
    first = json.loads(
        await prepared.tools.execute(
            ToolCall(
                "call_alert",
                "escalate_and_soft_hold",
                await _hold_args(start_date="2027-10-01"),
            ),
            ToolContext(tenant, conversation, channel="whatsapp"),
        )
    )
    assert first["ok"] is True
    assert alerts.calls == 1

    sessions = container.tenants._sessions  # type: ignore[attr-defined]
    async with sessions() as session:
        hold_count = await session.scalar(select(func.count()).select_from(Hold))
        pending = await session.scalar(
            select(func.count())
            .select_from(BrokerAlertOutbox)
            .where(BrokerAlertOutbox.status == "pending")
        )
    assert hold_count == 1
    assert pending == 1

    flushed = await yachting.flush_broker_alerts()
    assert flushed["delivered"] == 1
    assert alerts.calls == 2

    async with sessions() as session:
        completed = await session.scalar(
            select(func.count())
            .select_from(BrokerAlertOutbox)
            .where(BrokerAlertOutbox.status == "completed")
        )
    assert completed == 1
