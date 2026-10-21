from __future__ import annotations

import logging
import os

import httpx

from fivecent.config import Settings

logger = logging.getLogger(__name__)


def resolve_whatsapp_callback_url(
    settings: Settings, environ: dict[str, str] | None = None
) -> str:
    env = os.environ if environ is None else environ
    raw = (settings.whatsapp_callback_url or env.get("RENDER_EXTERNAL_URL") or "").strip()
    if not raw:
        return ""
    raw = raw.rstrip("/")
    if raw.endswith("/webhook"):
        return raw
    return f"{raw}/webhook"


async def sync_waba_webhook_override(
    settings: Settings, *, client: httpx.AsyncClient | None = None
) -> bool:
    """Point the WABA message callback at this process's public /webhook.

    App Dashboard subscriptions are not enough: WhatsApp delivers live messages
    to webhook_configuration.whatsapp_business_account (override_callback_uri).
    """
    callback = resolve_whatsapp_callback_url(settings)
    waba_id = settings.whatsapp_waba_id.strip()
    token = settings.whatsapp_access_token
    verify = settings.whatsapp_verify_token
    if not (callback and waba_id and token and verify):
        return False
    url = (
        f"https://graph.facebook.com/{settings.whatsapp_graph_version}/"
        f"{waba_id}/subscribed_apps"
    )
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    try:
        response = await http.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"override_callback_uri": callback, "verify_token": verify},
        )
        if response.status_code >= 400:
            logger.error(
                "WABA webhook override failed HTTP %s", response.status_code
            )
            return False
        logger.warning("WABA webhook override set callback=%s", callback)
        return True
    except Exception:
        logger.exception("WABA webhook override failed")
        return False
    finally:
        if owns_client:
            await http.aclose()
