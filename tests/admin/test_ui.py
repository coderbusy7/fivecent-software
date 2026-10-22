import json
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from fivecent.admin_auth import SESSION_IDLE_SECONDS, hash_password

APP = Path(__file__).resolve().parents[2] / "admin" / "app.py"
PASSWORD = "a-long-test-password"


@pytest.fixture
def ui(monkeypatch):
    import streamlit as st

    st.cache_resource.clear()
    monkeypatch.setenv("ADMIN_USERNAME", "operator")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password(PASSWORD))
    monkeypatch.setenv("ADMIN_API_TOKEN", "backend-test-token")
    monkeypatch.setenv("ADMIN_API_BASE", "http://admin-api.test")
    requests = []
    tenant = {
        "tenant_id": "agency-one",
        "agency_name": "Agency One",
        "home_port": "Cannes",
        "whatsapp_phone_number_id": "phone-one",
        "whatsapp_waba_id": "",
        "vat_rate": "0.20",
        "apa_rate": "0.30",
        "max_discount_pct": "0.08",
        "currency": "EUR",
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "feature_flags": {"enable_soft_hold": True},
        "is_active": True,
        "brokers": [],
    }
    response_status = [200]

    def handle(request):
        requests.append(request)
        if response_status[0] != 200:
            return httpx.Response(response_status[0], json={"detail": "API unavailable"})
        path = request.url.path
        if request.method == "PATCH":
            if "/fleet/" in path:
                return httpx.Response(200, json={"boat_id": "agency-one__demo", "active": False})
            tenant.update(json.loads(request.content))
        if path == "/internal/tenants":
            return httpx.Response(
                200, json={"total": 1, "offset": 0, "limit": 200, "items": [tenant]}
            )
        if path.endswith("/fleet"):
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "boat_id": "agency-one__demo",
                        "name": "Demo",
                        "active": True,
                    },
                )
            return httpx.Response(200, json=[])
        if path.endswith("/setup-status"):
            return httpx.Response(
                200,
                json={
                    "tenant_id": "agency-one",
                    "status": "draft",
                    "checks": {"has_active_boat": False, "whatsapp_access_token_present": True},
                },
            )
        if path.endswith("/fleet/seed-starter"):
            return httpx.Response(200, json={"boats": []})
        if path == "/internal/users":
            return httpx.Response(200, json=[])
        if path == "/internal/audit":
            return httpx.Response(200, json=[])
        if path == "/internal/metrics":
            return httpx.Response(
                200,
                json={
                    "failed_inbound": 0,
                    "failed_messages": 0,
                    "in_flight_messages": 0,
                    "oldest_failed_inbound_at": None,
                    "load_target": {"concurrent_conversations": 50},
                },
            )
        if path == "/internal/inbound/failed":
            return httpx.Response(200, json=[])
        if path.endswith("/usage"):
            return httpx.Response(200, json={"tenant_id": "agency-one", "days": 14, "items": []})
        if path.endswith("/conversations"):
            return httpx.Response(
                200, json={"total": 0, "offset": 0, "limit": 50, "items": []}
            )
        return httpx.Response(200, json=tenant)

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    yield AppTest.from_file(str(APP), default_timeout=10), requests, response_status
    st.cache_resource.clear()


def button(app, label):
    return next(item for item in app.button if item.label == label)


def sign_in(app, password=PASSWORD):
    app.text_input(key="login_username").set_value("operator")
    app.text_input(key="login_password").set_value(password)
    button(app, "Sign in").click().run()
    assert not app.exception


def test_signed_out_users_cannot_load_agencies_or_see_server_secrets(ui):
    app, requests, _ = ui
    app.run()
    assert not app.exception
    assert not requests
    assert {field.label for field in app.text_input} == {"Username", "Password"}
    assert not app.tabs
    assert not app.selectbox


def test_invalid_login_stays_signed_out_and_clears_password(ui):
    app, requests, _ = ui
    app.run()
    sign_in(app, "wrong-password")
    assert not requests
    assert "Invalid username or password." in [error.value for error in app.error]
    assert app.text_input(key="login_password").value == ""
    assert not app.tabs


def test_valid_login_dashboard_save_and_logout(ui):
    app, requests, _ = ui
    app.run()
    sign_in(app)
    assert len(app.tabs) == 12
    # Top agency summary metrics only (ops tab adds more).
    assert [metric.value for metric in app.metric][:3] == ["1", "1", "0"]
    assert all(request.headers["Authorization"] == "Bearer backend-test-token" for request in requests)
    assert not any(field.label in {"Admin token", "API base URL"} for field in app.text_input)
    assert "login_password" not in app.session_state
    agency_name = next(field for field in app.text_input if field.value == "Agency One")
    agency_name.set_value("Updated Agency")
    button(app, "Save identity").click().run()
    assert not app.exception
    patch = next(request for request in requests if request.method == "PATCH")
    assert json.loads(patch.content)["agency_name"] == "Updated Agency"
    request_count = len(requests)
    button(app, "Sign out").click().run()
    assert not app.exception
    assert len(requests) == request_count
    assert not app.tabs
    assert "admin_session" not in app.session_state
    assert app.text_input(key="login_password").value == ""


def test_expired_session_cannot_issue_more_api_requests(ui):
    app, requests, _ = ui
    app.run()
    sign_in(app)
    session = app.session_state["admin_session"]
    session["last_seen"] = time.monotonic() - SESSION_IDLE_SECONDS
    request_count = len(requests)
    app.run()
    assert not app.exception
    assert len(requests) == request_count
    assert not app.tabs
    assert "expired" in app.error[0].value


def test_api_authorization_failure_revokes_ui_session(ui):
    app, _, status = ui
    app.run()
    status[0] = 401
    sign_in(app)
    assert not app.tabs
    assert "admin_session" not in app.session_state
    assert "access was denied" in app.error[0].value


def test_api_failure_is_not_presented_as_an_empty_agency_list(ui):
    app, _, status = ui
    app.run()
    status[0] = 503
    sign_in(app)
    assert not app.tabs
    assert "503" in app.error[0].value
    assert not app.info


def test_missing_login_configuration_fails_closed(ui, monkeypatch):
    app, requests, _ = ui
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "")
    app.run()
    assert not app.exception
    assert not requests
    assert not app.text_input
    assert "not configured" in app.error[0].value


@pytest.mark.parametrize(
    ("submit", "method", "path", "field", "value"),
    [
        ("Save money rules", "PATCH", "/internal/tenants/agency-one", "vat_rate", "0.2"),
        ("Save AI and service", "PATCH", "/internal/tenants/agency-one", "llm_provider", "openai"),
        ("Save flags", "PATCH", "/internal/tenants/agency-one", "feature_flags", {"enable_soft_hold": True}),
        ("Save desk chat id", "PATCH", "/internal/tenants/agency-one", "telegram_chat_id", None),
        ("Add broker", "POST", "/internal/tenants/agency-one/brokers", "role", "broker"),
        ("Create", "POST", "/internal/tenants", "service_type", "yachting"),
    ],
)
def test_admin_forms_still_submit_authenticated_requests(ui, submit, method, path, field, value):
    app, requests, _ = ui
    app.run()
    sign_in(app)
    button(app, submit).click().run()
    assert not app.exception
    request = next(request for request in requests if request.method == method)
    assert request.url.path == path
    assert request.headers["Authorization"] == "Bearer backend-test-token"
    submitted = json.loads(request.content)[field]
    if isinstance(value, dict):
        assert submitted.items() >= value.items()
    else:
        assert submitted == value


def test_throttling_cannot_be_bypassed_with_a_new_browser_session(ui):
    app, requests, _ = ui
    app.run()
    for _ in range(5):
        sign_in(app, "wrong-password")
    other_browser = AppTest.from_file(str(APP), default_timeout=10).run()
    sign_in(other_browser)
    assert not requests
    assert "Too many failed attempts" in other_browser.error[0].value


def test_network_failure_shows_an_error_without_rendering_forms(ui, monkeypatch):
    app, requests, _ = ui

    def fail(_transport, request):
        raise httpx.ConnectError("Connection refused", request=request)

    monkeypatch.setattr(httpx.MockTransport, "handle_request", fail)
    app.run()
    sign_in(app)
    assert not requests
    assert "Cannot reach the admin API" in app.error[0].value
    assert not app.tabs
