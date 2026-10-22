import asyncio
import json
from datetime import datetime, timezone

from fivecent.application.ports import LLMResult
from fivecent.application.tools import ToolCall, ToolContext
from fivecent.application.turn import handle_turn
from fivecent.adapters.llm.openai_chat import ScriptedLLM
from fivecent.domain.events import Channel, InboundMessageEvent
from fivecent.messaging.errors import SessionWindowClosedError


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


async def test_repeat_greeting_does_not_send_again(container) -> None:
    first = await handle_turn(_event(text="Hi"), container)
    assert first.skipped is False
    second = await handle_turn(
        _event(text="Hi", provider_message_id="wamid.hi-2"), container
    )
    assert second.skipped is True
    assert second.reason == "repeat_greeting"


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


async def test_request_broker_followup_tool(container) -> None:
    conv = await container.conversations.claim_inbound(
        _event(provider_message_id="wamid.followup"), "user"
    )
    tenant = await container.tenants.get("port-vauban")
    prepared = await container.services.get("yachting").prepare(tenant)
    raw = await prepared.tools.execute(
        ToolCall(
            id="call_fu",
            name="request_broker_followup",
            arguments='{"destination":"Monaco","contact_preference":"whatsapp","notes":"Aug weekend"}',
        ),
        ToolContext(
            tenant=tenant,
            conversation_id=conv,
            channel="whatsapp",
            user_channel_id="+33612345678",
        ),
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["destination"] == "Monaco"
    assert payload["contact_preference"] == "whatsapp"
    assert payload["guest_channel"] == "whatsapp"
    assert payload["guest_user_id"] == "+33612345678"
    assert payload["wa_me"] == "https://wa.me/33612345678"
    assert isinstance(payload["alert_brokers"], list)
    assert len(payload["alert_brokers"]) >= 1



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
        ToolContext(
            tenant,
            first_conversation,
            channel="whatsapp",
            user_channel_id="+33612345678",
        ),
    )
    second = await prepared.tools.execute(
        ToolCall("call_hold_2", "escalate_and_soft_hold", arguments),
        ToolContext(
            tenant,
            second_conversation,
            channel="whatsapp",
            user_channel_id="+33611111111",
        ),
    )

    first_payload = json.loads(first)
    assert first_payload["ok"] is True
    assert first_payload["wa_me"] == "https://wa.me/33612345678"
    assert isinstance(first_payload["alert_brokers"], list)
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


async def test_off_topic_reaches_llm(container) -> None:
    llm = container.providers.get("openai")
    assert isinstance(llm, ScriptedLLM)
    result = await handle_turn(
        _event(provider_message_id="wamid.off.1", text="What's the weather in Munnar?"),
        container,
    )
    assert result.skipped is False
    assert result.reply == "How can I help with a charter?"
    assert llm.calls


async def test_tenant_model_is_selected_for_each_turn(container) -> None:
    await container.tenants.update_tenant("port-vauban", llm_model="test-model")

    result = await handle_turn(_event(provider_message_id="wamid.model"), container)

    llm = container.providers.get("openai")
    assert isinstance(llm, ScriptedLLM)
    assert result.reply == "How can I help with a charter?"
    assert llm.calls[0]["model"] == "test-model"


async def test_debounce_coalesces_burst_into_one_reply(container) -> None:
    container.debounce_seconds = 0.15
    llm = container.providers.get("openai")
    assert isinstance(llm, ScriptedLLM)

    async def send(text: str, mid: str):
        return await handle_turn(
            _event(text=text, provider_message_id=mid, user_channel_id="+336777"),
            container,
        )

    first_t = asyncio.create_task(send("Hi", "wamid.burst.1"))
    await asyncio.sleep(0.03)
    second_t = asyncio.create_task(send("Looking for a boat this Saturday", "wamid.burst.2"))
    await asyncio.sleep(0.03)
    third_t = asyncio.create_task(send("Around 20m for 8 people", "wamid.burst.3"))
    results = await asyncio.gather(first_t, second_t, third_t)

    winners = [r for r in results if not r.skipped]
    losers = [r for r in results if r.skipped]
    assert len(winners) == 1
    assert len(losers) == 2
    assert all(r.reason == "debounced" for r in losers)
    assert len(llm.calls) == 1
    user_msgs = [m.content for m in llm.calls[0]["messages"] if m.role == "user"]
    assert user_msgs[-1] == "Hi\nLooking for a boat this Saturday\nAround 20m for 8 people"
    sender = container.sender
    assert len(sender.sent) == 1


async def test_session_window_closed_notifies_brokers_and_skips(container) -> None:
    class WindowClosedSender:
        async def send(
            self, tenant_id: str, channel: Channel, user_channel_id: str, text: str
        ) -> str:
            raise SessionWindowClosedError(
                tenant_id=tenant_id,
                user_channel_id=user_channel_id,
                detail="131047",
            )

    class CaptureAlerts:
        def __init__(self) -> None:
            self.handoffs: list[tuple[str, dict]] = []

        async def notify_handoff(self, tenant_id: str, payload: dict) -> bool:
            self.handoffs.append((tenant_id, payload))
            return True

    alerts = CaptureAlerts()
    container.sender = WindowClosedSender()
    container.ops_alerts = alerts

    result = await handle_turn(
        _event(
            provider_message_id="wamid.window",
            channel="whatsapp",
            user_channel_id="33612345678",
            text="Still interested?",
        ),
        container,
    )
    assert result.skipped is True
    assert result.reason == "session_window_closed"
    assert len(alerts.handoffs) == 1
    tenant_id, payload = alerts.handoffs[0]
    assert tenant_id == "port-vauban"
    assert payload["reason"] == "session_window_closed"
    assert payload["code"] == 131047
    assert payload["wa_me"] == "https://wa.me/33612345678"
    assert payload["draft_reply"]
    assert isinstance(payload["alert_brokers"], list)


async def test_debounce_bypassed_for_business_echo(container) -> None:
    container.debounce_seconds = 5.0
    started = asyncio.get_event_loop().time()
    result = await handle_turn(
        _event(
            is_business_echo=True,
            text="I'll take this",
            provider_message_id="wamid.echo.fast",
        ),
        container,
    )
    elapsed = asyncio.get_event_loop().time() - started
    assert result.skipped is True
    assert result.reason == "echo"
    assert elapsed < 1.0
