from __future__ import annotations

import logging
import re
from typing import Callable

from fastapi import APIRouter, Header, HTTPException, Request

from fivecent.adapters.persistence.tenants import SqlTenantRepo
from fivecent.application.ports import Container
from fivecent.config import Settings
from fivecent.messaging.plugin_telegram.outbound import send_telegram_text

logger = logging.getLogger(__name__)

_MUTE = re.compile(r"^/mute[_+](?P<phone>\d+)[_+](?P<tenant>[A-Za-z0-9_-]+)\s*$")
_START = re.compile(r"^/start(?:@\w+)?(?:\s+(?P<tenant>[A-Za-z0-9_-]+))?\s*$")


def register_telegram_routes(
    app,
    *,
    get_container: Callable[[], Container],
    get_settings_fn: Callable[[], Settings],
) -> None:
    router = APIRouter(prefix="/telegram", tags=["telegram"])

    @router.post("/webhook")
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str = Header(default=""),
    ) -> dict[str, bool]:
        settings = get_settings_fn()
        if not settings.telegram_bot_token:
            raise HTTPException(status_code=503, detail="Telegram bot is not configured.")
        expected = (settings.telegram_webhook_secret or "").strip()
        if expected and x_telegram_bot_api_secret_token != expected:
            raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret.")

        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON.")

        message = payload.get("message") or payload.get("edited_message") or {}
        if not isinstance(message, dict):
            return {"ok": True}
        text = str(message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not text or chat_id is None:
            return {"ok": True}

        container = get_container()
        tenants = container.tenants
        if not isinstance(tenants, SqlTenantRepo):
            logger.error("telegram webhook requires SqlTenantRepo")
            return {"ok": True}

        start = _START.match(text)
        if start:
            tenant_id = (start.group("tenant") or "").strip()
            if not tenant_id:
                await send_telegram_text(
                    bot_token=settings.telegram_bot_token,
                    chat_id=str(chat_id),
                    text=(
                        "Send an agency invite link to connect.\n"
                        f"Example: https://t.me/{settings.telegram_bot_username}?start=port-vauban"
                    ),
                )
                return {"ok": True}
            ctx = await tenants.get(tenant_id)
            if ctx is None or not ctx.is_active:
                await send_telegram_text(
                    bot_token=settings.telegram_bot_token,
                    chat_id=str(chat_id),
                    text=f"Unknown or inactive agency `{tenant_id}`.",
                )
                return {"ok": True}
            await tenants.set_tenant_telegram_chat_id(tenant_id, str(chat_id))
            await send_telegram_text(
                bot_token=settings.telegram_bot_token,
                chat_id=str(chat_id),
                text=(
                    f"Connected to {ctx.agency_name}!\n"
                    "You will receive real-time charter alerts and 24h soft-holds here."
                ),
            )
            logger.info(
                "telegram desk linked tenant=%s chat_id=%s", tenant_id, chat_id
            )
            return {"ok": True}

        mute = _MUTE.match(text)
        if mute:
            phone = mute.group("phone")
            tenant_id = mute.group("tenant")
            ctx = await tenants.get(tenant_id)
            if ctx is None:
                await send_telegram_text(
                    bot_token=settings.telegram_bot_token,
                    chat_id=str(chat_id),
                    text=f"Unknown agency `{tenant_id}`.",
                )
                return {"ok": True}
            # WhatsApp Cloud API uses digit-only `from` ids; mute commands match that.
            await container.cache.set_human_active(tenant_id, "whatsapp", phone)
            await send_telegram_text(
                bot_token=settings.telegram_bot_token,
                chat_id=str(chat_id),
                text=(
                    f"Elena muted for 24 hours for +{phone} ({ctx.agency_name}). "
                    "You have full control on WhatsApp."
                ),
            )
            logger.info(
                "telegram mute tenant=%s phone=%s chat_id=%s", tenant_id, phone, chat_id
            )
            return {"ok": True}

        return {"ok": True}

    app.include_router(router)
