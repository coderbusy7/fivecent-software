import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from fivecent.messaging.graph import verify_signature
from fivecent.messaging.plugin_instagram import normalize_instagram_payload
from fivecent.messaging.plugin_meta import normalize_messenger_payload
from fivecent.messaging.plugin_whatsapp import normalize_whatsapp_payload
from fivecent.api.app import create_app

SAMPLE = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "215589313241560883",
            "changes": [
                {
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15551797781",
                            "phone_number_id": "7794189252778687",
                        },
                        "contacts": [
                            {
                                "profile": {"name": "Jessica Laverdetman"},
                                "wa_id": "13557825698",
                            }
                        ],
                        "messages": [
                            {
                                "from": "17863559966",
                                "id": "wamid.HBgLMTc4NjM1NTk5NjYVAGHAYWYET688aASGNTI1QzZFQjhEMDk2QQA=",
                                "timestamp": "1758254144",
                                "text": {"body": "Hi!"},
                                "type": "text",
                            }
                        ],
                    },
                    "field": "messages",
                }
            ],
        }
    ],
}


def test_normalize_project_md_payload() -> None:
    events = normalize_whatsapp_payload(
        SAMPLE, tenant_for_phone_number_id=lambda pid: "port-vauban"
    )
    assert len(events) == 1
    assert events[0].channel == "whatsapp"
    assert events[0].user_channel_id == "17863559966"
    assert events[0].text == "Hi!"
    assert events[0].tenant_id == "port-vauban"


def test_normalize_live_meta_hi_payload() -> None:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "2093966711993530",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15552014794",
                                "phone_number_id": "1350102171528381",
                            },
                            "contacts": [{"wa_id": "33621611871"}],
                            "messages": [
                                {
                                    "from": "33621611871",
                                    "id": "wamid.test-hi",
                                    "timestamp": "1792605379",
                                    "text": {"body": "Hi"},
                                    "type": "text",
                                    "from_user_id": "FR.1078108675065602",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    events = normalize_whatsapp_payload(
        payload, tenant_for_phone_number_id=lambda pid: "port-vauban" if pid == "1350102171528381" else None
    )
    assert len(events) == 1
    assert events[0].text == "Hi"
    assert events[0].user_channel_id == "33621611871"
    assert events[0].tenant_id == "port-vauban"


def test_unknown_phone_number_dropped() -> None:
    events = normalize_whatsapp_payload(SAMPLE, tenant_for_phone_number_id=lambda pid: None)
    assert events == []


def test_signature_roundtrip() -> None:
    body = b'{"ok":true}'
    secret = "test-app-secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(secret, body, f"sha256={digest}") is True
    assert verify_signature(secret, body, "sha256=deadbeef") is False


def test_channel_stubs() -> None:
    try:
        normalize_instagram_payload({}, "port-vauban")
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass
    try:
        normalize_messenger_payload({}, "port-vauban")
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass


def test_webhook_rejects_bad_hmac(container, settings) -> None:
    app = create_app(
        container=container,
        settings=settings,
        phone_map={settings.whatsapp_phone_number_id: settings.seed_tenant_id},
    )
    with TestClient(app) as client:
        response = client.post("/webhook", content=b"{}", headers={"X-Hub-Signature-256": "sha256=nope"})
        assert response.status_code == 403


class ListQueue:
    def __init__(self) -> None:
        self.events: list = []

    async def enqueue(self, event) -> None:
        self.events.append(event)


def test_webhook_accepts_signed_payload(container, settings) -> None:
    queue = ListQueue()
    container.queue = queue
    app = create_app(
        container=container,
        settings=settings,
        phone_map={settings.whatsapp_phone_number_id: settings.seed_tenant_id},
    )
    body = json.dumps(SAMPLE).encode()
    digest = hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            content=body,
            headers={"X-Hub-Signature-256": f"sha256={digest}"},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert len(queue.events) == 1
        assert queue.events[0].text == "Hi!"


def test_verify_webhook_handshake(container, settings) -> None:
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "challenge-token",
            },
        )
        assert response.status_code == 200
        assert response.text == "challenge-token"


def test_registered_channel_route_accepts_whatsapp(container, settings) -> None:
    queue = ListQueue()
    container.queue = queue
    app = create_app(
        container=container,
        settings=settings,
        phone_map={settings.whatsapp_phone_number_id: settings.seed_tenant_id},
    )
    body = json.dumps(SAMPLE).encode()
    digest = hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"X-Hub-Signature-256": f"sha256={digest}"},
        )
        assert response.status_code == 200
        assert len(queue.events) == 1


def test_unregistered_channel_webhook_returns_404(container, settings) -> None:
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        response = client.post("/webhooks/telegram", content=b"{}")
    assert response.status_code == 404


def test_instagram_plugin_is_registered_but_not_configured(container, settings) -> None:
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        response = client.post("/webhooks/instagram", content=b"{}")
    assert response.status_code == 503
