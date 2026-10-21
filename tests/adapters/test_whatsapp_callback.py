import httpx
import pytest

from fivecent.config import Settings
from fivecent.messaging.plugin_whatsapp.callback import (
    resolve_whatsapp_callback_url,
    sync_waba_webhook_override,
)


def _settings(**kwargs) -> Settings:
    values = dict(
        _env_file=None,
        whatsapp_access_token="token",
        whatsapp_verify_token="verify-me",
        whatsapp_waba_id="2093966711993530",
        whatsapp_graph_version="v21.0",
        whatsapp_callback_url="",
    )
    values.update(kwargs)
    return Settings(**values)


def test_resolve_appends_webhook_path() -> None:
    settings = _settings(whatsapp_callback_url="https://fivecent-software.onrender.com")
    assert (
        resolve_whatsapp_callback_url(settings, environ={})
        == "https://fivecent-software.onrender.com/webhook"
    )


def test_resolve_uses_render_external_url() -> None:
    settings = _settings()
    assert (
        resolve_whatsapp_callback_url(
            settings, environ={"RENDER_EXTERNAL_URL": "https://fivecent-software.onrender.com/"}
        )
        == "https://fivecent-software.onrender.com/webhook"
    )


def test_resolve_skips_when_unset() -> None:
    assert resolve_whatsapp_callback_url(_settings(), environ={}) == ""


@pytest.mark.asyncio
async def test_sync_posts_override_callback() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await sync_waba_webhook_override(
            _settings(whatsapp_callback_url="https://fivecent-software.onrender.com/webhook"),
            client=client,
        )
    assert ok is True
    assert "2093966711993530/subscribed_apps" in seen["url"]
    assert "fivecent-software.onrender.com/webhook" in seen["json"]
    assert "verify-me" in seen["json"]


@pytest.mark.asyncio
async def test_sync_noop_without_callback() -> None:
    ok = await sync_waba_webhook_override(_settings(whatsapp_callback_url=""))
    assert ok is False
