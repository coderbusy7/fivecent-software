from datetime import datetime, timezone
import json

from fivecent.application.ports import LLMResult
from fivecent.application.tools import ToolCall, ToolContext
from fivecent.application.turn import handle_turn
from fivecent.adapters.llm.openai_chat import ScriptedLLM
from fivecent.domain.events import InboundMessageEvent


def _event(**kwargs) -> InboundMessageEvent:
    payload = {
        "tenant_id": "port-vauban",
        "channel": "test",
        "user_channel_id": "+33612345678",
        "provider_message_id": "wamid.1",
        "text": "Hello",
        "timestamp": datetime.now(timezone.utc),
    }
    payload.update(kwargs)
    return InboundMessageEvent.model_validate(payload)


async def test_hitl_skips_llm(container) -> None:
    await container.cache.set_human_active("port-vauban", "test", "+33612345678")
    result = await handle_turn(_event(), container)
    assert result.skipped is True
    assert result.reason == "hitl"
    llm = container.providers.get("openai")
    assert isinstance(llm, ScriptedLLM)
    assert llm.calls == []


async def test_duplicate_wamid_skipped(container) -> None:
    first = await handle_turn(_event(), container)
    assert first.skipped is False
    second = await handle_turn(_event(text="Again"), container)
    assert second.skipped is True
    assert second.reason == "duplicate"


async def test_business_echo_sets_hitl(container) -> None:
    echo = await handle_turn(
        _event(is_business_echo=True, text="I'll take this", provider_message_id="wamid.echo"),
        container,
    )
    assert echo.skipped is True
    assert echo.reason == "echo"
    result = await handle_turn(_event(provider_message_id="wamid.2", text="Still here?"), container)
    assert result.reason == "hitl"


async def test_pax_cap_via_tool(container) -> None:
    conv = await container.conversations.claim_inbound(
        _event(provider_message_id="wamid.tool"), "user"
    )
    tenant = await container.tenants.get("port-vauban")
    prepared = await container.services.get("yachting").prepare(tenant)
    raw = await prepared.tools.execute(
        ToolCall(
            id="call_pax",
            name="check_fleet_availability",
            arguments='{"pax":13,"start_date":"2026-06-06"}',
        ),
        ToolContext(tenant=tenant, conversation_id=conv),
    )
    assert "pax_cap" in raw


async def test_soft_hold_rejects_overlapping_active_hold(container) -> None:
    tenant = await container.tenants.get("port-vauban")
    assert tenant is not None
    prepared = await container.services.get("yachting").prepare(tenant)
    arguments = (
        '{"customer_name":"Ada","email":"ada@example.com",'
        '"boat_id":"lagoon-450","start_date":"2027-06-06","days":2}'
    )
    first_conversation = await container.conversations.claim_inbound(
        _event(provider_message_id="wamid.hold.1"), "user"
    )
    second_conversation = await container.conversations.claim_inbound(
        _event(
            provider_message_id="wamid.hold.2",
            user_channel_id="+33611111111",
        ),
        "user",
    )
    first = await prepared.tools.execute(
        ToolCall("call_hold_1", "escalate_and_soft_hold", arguments),
        ToolContext(tenant, first_conversation),
    )
    second = await prepared.tools.execute(
        ToolCall("call_hold_2", "escalate_and_soft_hold", arguments),
        ToolContext(tenant, second_conversation),
    )

    assert json.loads(first)["ok"] is True
    assert json.loads(second)["code"] == "hold_conflict"


async def test_scripted_tool_quote(container) -> None:
    llm = container.providers.get("openai")
    assert isinstance(llm, ScriptedLLM)
    llm.enqueue(
        LLMResult(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="calculate_itemized_quote",
                    arguments='{"boat_id":"lagoon-450","start_date":"2026-06-06","days":2}',
                )
            ],
            tokens_in=10,
            tokens_out=5,
        )
    )
    llm.enqueue(
        LLMResult(
            content="Lagoon 450 for two June days is 6240.00 EUR including VAT and APA.",
            tool_calls=[],
            tokens_in=12,
            tokens_out=20,
        )
    )
    result = await handle_turn(_event(text="Quote the Lagoon 450 for 2 days from 2026-06-06"), container)
    assert result.skipped is False
    assert result.reply is not None
    assert "6240" in result.reply
    assert llm.calls[0]["messages"][0].role == "system"
    assert llm.calls[0]["tools"][0].name == "check_fleet_availability"
    assert llm.calls[0]["model"] == "gpt-4o-mini"


async def test_tenant_model_is_selected_for_each_turn(container) -> None:
    await container.tenants.update_tenant("port-vauban", llm_model="test-model")

    result = await handle_turn(_event(provider_message_id="wamid.model"), container)

    llm = container.providers.get("openai")
    assert isinstance(llm, ScriptedLLM)
    assert result.reply == "How can I help with a charter?"
    assert llm.calls[0]["model"] == "test-model"
