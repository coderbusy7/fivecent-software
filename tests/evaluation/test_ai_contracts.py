"""Deterministic AI/tool contracts — not live LLM quality grades.

These prove required tool availability, pricing/pax safeguards, and stable
customer-facing error codes. Live model scoring belongs under @pytest.mark.ai_live.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from fivecent.application.tools import ToolCall, ToolContext
from fivecent.domain.events import InboundMessageEvent
from fivecent.services.plugin_yachting.domain.fleet import check_fleet_availability
from fivecent.services.plugin_yachting.domain.quoting import Boat

REQUIRED_TOOLS = {
    "check_fleet_availability",
    "calculate_itemized_quote",
    "evaluate_negotiation_floor",
    "escalate_and_soft_hold",
    "request_broker_followup",
}


def _event(**kwargs) -> InboundMessageEvent:
    payload = {
        "tenant_id": "port-vauban",
        "channel": "test",
        "user_channel_id": "+33619999999",
        "provider_message_id": "wamid.eval.1",
        "text": "eval",
        "timestamp": datetime.now(timezone.utc),
    }
    payload.update(kwargs)
    return InboundMessageEvent.model_validate(payload)


@pytest.mark.asyncio
async def test_yachting_exposes_required_tools(container) -> None:
    tenant = await container.tenants.get("port-vauban")
    prepared = await container.services.get("yachting").prepare(tenant)
    names = {spec.name for spec in prepared.tools.specs()}
    missing = REQUIRED_TOOLS - names
    assert not missing, f"missing tools: {sorted(missing)}"


@pytest.mark.asyncio
async def test_pax_cap_is_hard_reject(container) -> None:
    tenant = await container.tenants.get("port-vauban")
    prepared = await container.services.get("yachting").prepare(tenant)
    conv = await container.conversations.claim_inbound(
        _event(provider_message_id="wamid.eval.pax"), "user"
    )
    raw = await prepared.tools.execute(
        ToolCall(
            "call_pax",
            "check_fleet_availability",
            '{"pax":13,"start_date":"2026-06-06"}',
        ),
        ToolContext(tenant, conv),
    )
    assert "pax_cap" in raw


def test_domain_pax_cap_without_llm() -> None:
    boat = Boat(
        id="x",
        name="X",
        length_m=Decimal("14"),
        capacity=12,
        low_season_daily_rate=Decimal("1000"),
        high_season_daily_rate=Decimal("2000"),
        skipper_daily_rate=Decimal("350"),
        active=True,
    )
    result = check_fleet_availability(
        boats=[boat],
        holds=[],
        pax=13,
        start_date=date(2026, 6, 6),
        days=1,
        length_m=None,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "pax_cap"


@pytest.mark.asyncio
async def test_soft_hold_customer_facing_codes_are_stable(container) -> None:
    tenant = await container.tenants.get("port-vauban")
    prepared = await container.services.get("yachting").prepare(tenant)
    conv = await container.conversations.claim_inbound(
        _event(provider_message_id="wamid.eval.hold", user_channel_id="+33618888888"),
        "user",
    )
    raw = await prepared.tools.execute(
        ToolCall(
            "call_bad",
            "escalate_and_soft_hold",
            '{"customer_name":"","email":"bad","boat_id":"lagoon-450","start_date":"2027-01-01","days":1}',
        ),
        ToolContext(tenant, conv),
    )
    payload = json.loads(raw)
    assert payload.get("ok") is False
    assert payload.get("code") in {"missing_name", "missing_email"}
