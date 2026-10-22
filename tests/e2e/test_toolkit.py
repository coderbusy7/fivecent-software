"""Unit tests for the e2e toolkit (no live API calls)."""

from e2e.checks import ContainsAny, MinLength, StatusIn, any_of
from e2e.client import WhatsAppWebhookClient
from e2e.config import E2EConfig
from e2e.scenarios.registry import get_scenario, list_scenarios


def test_checks_evaluate_reply() -> None:
    assert any_of("vat", "apa").evaluate("Total with VAT").ok
    assert not ContainsAny(("nope",)).evaluate("hello").ok
    assert MinLength(5).evaluate("hello!").ok
    assert StatusIn(("ok",)).evaluate("", status="ok").ok


def test_scenarios_registered() -> None:
    ids = {s.id for s in list_scenarios()}
    assert "greeting" in ids
    assert "pilot_suite" in ids
    assert get_scenario("quote").turns


def test_webhook_payload_uses_test_recipient() -> None:
    config = E2EConfig(
        api_base="http://example.test",
        phone="33621611871",
        whatsapp_app_secret="secret",
        whatsapp_phone_number_id="1350102171528381",
        pause_seconds=0,
        mode="webhook",
    )
    client = WhatsAppWebhookClient(config)
    payload = client._build_payload("hi", "33621611871")
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    assert message["from"] == "33621611871"
    assert message["text"]["body"] == "hi"
    meta = payload["entry"][0]["changes"][0]["value"]["metadata"]
    assert meta["phone_number_id"] == "1350102171528381"
