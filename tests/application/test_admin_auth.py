import pytest
from fastapi.testclient import TestClient

from fivecent.api.app import create_app


@pytest.mark.parametrize(
    "authorization",
    ["", "******", "Bearer wrong-token", "admin-secret", "Bearer "],
)
def test_internal_admin_rejects_invalid_credentials(container, settings, authorization):
    settings.admin_api_token = "admin-secret"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        response = client.get(
            "/internal/tenants", headers={"Authorization": authorization}
        )
    assert response.status_code == 401


def test_internal_admin_accepts_configured_bearer_token(container, settings):
    settings.admin_api_token = "admin-secret"
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        response = client.get(
            "/internal/tenants", headers={"Authorization": "Bearer admin-secret"}
        )
    assert response.status_code == 200


def test_internal_admin_disabled_without_token(container, settings):
    settings.admin_api_token = ""
    app = create_app(container=container, settings=settings, phone_map={})
    with TestClient(app) as client:
        response = client.get(
            "/internal/tenants", headers={"Authorization": "Bearer anything"}
        )
    assert response.status_code == 503
