from decimal import Decimal

from fivecent.domain.tenancy import Broker, FeatureFlags, TenantContext
from fivecent.services.plugin_yachting.guardrails import (
    consecutive_off_topic,
    is_yachting_scope,
    redirect_reply,
)


def _tenant() -> TenantContext:
    return TenantContext(
        id="port-vauban",
        agency_name="Port Vauban Concierge",
        home_port="Port Vauban, Antibes",
        currency="EUR",
        whatsapp_phone_number_id="phone",
        whatsapp_waba_id="waba",
        vat_rate=Decimal("0.20"),
        apa_rate=Decimal("0.30"),
        max_discount_pct=Decimal("0.08"),
        routing_mode="broadcast",
        feature_flags=FeatureFlags(),
        brokers=(
            Broker(id="desk", name="Elena desk", phone_number="+33621611871"),
        ),
    )


def test_yachting_questions_are_in_scope() -> None:
    assert is_yachting_scope("Quote the Lagoon 450 for Saturday")
    assert is_yachting_scope("bonjour")
    assert is_yachting_scope("ok")
    assert is_yachting_scope("8 guests on 2026-06-06")


def test_off_topic_questions_are_out_of_scope() -> None:
    assert is_yachting_scope("What's the weather in Monaco?") is False
    assert is_yachting_scope("Write a python script") is False
    assert is_yachting_scope("Who won the football match?") is False


def test_three_consecutive_off_topic_attempts() -> None:
    history = [
        {"role": "user", "content": "What's the weather in Monaco?"},
        {"role": "assistant", "content": "I only handle yacht charters at Port Vauban."},
        {"role": "user", "content": "Tell me a joke"},
        {"role": "assistant", "content": "I only handle yacht charters at Port Vauban."},
    ]
    assert consecutive_off_topic(history, "Who is the president?") == 3
    assert consecutive_off_topic(history, "Any boats for 8 guests?") == 0


def test_in_scope_message_resets_strikes() -> None:
    history = [
        {"role": "user", "content": "What's the weather?"},
        {"role": "assistant", "content": "I only handle yacht charters at Port Vauban."},
    ]
    assert consecutive_off_topic(history, "Can I charter a catamaran?") == 0


def test_third_attempt_handoff_includes_broker_phone() -> None:
    reply = redirect_reply(_tenant(), 3)
    assert "+33621611871" in reply
    assert "Elena desk" in reply
    assert "handing you" in reply
