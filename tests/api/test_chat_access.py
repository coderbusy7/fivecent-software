"""ED-9: production gates for the public test chat endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fivecent.api.app import create_app


def _chat_body() -> dict:
    return {"phone": "+33612345678", "message": "hello"}


def test_chat_open_in_development(container, settings) -> None:
    settings.app_env = "development"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        response = client.post("/chat", json=_chat_body())
        assert response.status_code == 200


def test_chat_disabled_in_production_by_default(container, settings) -> None:
    settings.app_env = "production"
    settings.enable_test_chat = False
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        response = client.post("/chat", json=_chat_body())
        assert response.status_code == 404


def test_chat_requires_bearer_when_enabled_in_production(container, settings) -> None:
    settings.app_env = "production"
    settings.enable_test_chat = True
    settings.admin_api_token = "chat-secret"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        denied = client.post("/chat", json=_chat_body())
        assert denied.status_code == 401
        allowed = client.post(
            "/chat",
            headers={"Authorization": "Bearer chat-secret"},
            json=_chat_body(),
        )
        assert allowed.status_code == 200
