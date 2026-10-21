from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def send_telegram_text(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    client: Optional[httpx.AsyncClient] = None,
) -> bool:
    if not bot_token or not chat_id or not text:
        return False
    owns = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        response = await http.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )
        if response.status_code >= 400:
            detail = response.text.replace(bot_token, "[REDACTED]")
            logger.error("Telegram reply failed HTTP %s: %s", response.status_code, detail)
            return False
        return True
    finally:
        if owns:
            await http.aclose()
