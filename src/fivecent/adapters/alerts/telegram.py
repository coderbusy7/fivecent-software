from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def format_telegram_alert(tenant_id: str, kind: str, payload: dict) -> str:
    lines = [f"Fivecent · {kind}", f"Agency: {tenant_id}"]
    if kind == "hold":
        lines.append(
            "24h option: "
            f"{payload.get('boat_id')} · {payload.get('start_date')} · "
            f"{payload.get('days')} day(s)"
        )
        if payload.get("expires_at"):
            lines.append(f"Expires: {payload['expires_at']}")
        if payload.get("lead_id"):
            lines.append(f"Lead: {payload['lead_id']}")
    elif kind == "followup":
        lines.append(f"Destination: {payload.get('destination') or '—'}")
        pref = payload.get("contact_preference")
        if pref:
            lines.append(f"Contact: {pref}")
        notes = str(payload.get("notes") or "").strip()
        if notes:
            lines.append(f"Notes: {notes}")
        name = str(payload.get("customer_name") or "").strip()
        if name:
            lines.append(f"Guest: {name}")
    elif kind == "handoff" and payload.get("reason") == "session_window_closed":
        lines.append("WhatsApp 24h session window closed (Meta 131047).")
        lines.append("Free-form reply blocked — send an approved template message.")
        draft = str(payload.get("draft_reply") or "").strip()
        if draft:
            lines.append(f"Draft (not sent): {draft[:500]}")
    else:
        reason = payload.get("reason") or kind
        lines.append(f"Reason: {reason}")

    if payload.get("quote_id"):
        lines.append(f"Quote: {payload['quote_id']}")
    wa_me = payload.get("wa_me")
    if wa_me:
        lines.append(f"WhatsApp: {wa_me}")
    elif payload.get("guest_user_id"):
        lines.append(f"Guest id: {payload['guest_user_id']}")
    return "\n".join(lines)


def telegram_chat_ids(payload: dict) -> list[str]:
    brokers = payload.get("alert_brokers") or []
    if not isinstance(brokers, list):
        return []
    ids: list[str] = []
    for broker in brokers:
        if not isinstance(broker, dict):
            continue
        chat_id = broker.get("telegram_chat_id")
        if chat_id is None:
            continue
        text = str(chat_id).strip()
        if text:
            ids.append(text)
    return ids


class TelegramBrokerAlerts:
    def __init__(
        self,
        *,
        bot_token: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._token = bot_token
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def deliver(self, tenant_id: str, kind: str, payload: dict) -> None:
        if not self._token:
            return
        chat_ids = telegram_chat_ids(payload)
        if not chat_ids:
            logger.warning(
                "telegram alert skipped: no telegram_chat_id kind=%s tenant=%s",
                kind,
                tenant_id,
            )
            return
        text = format_telegram_alert(tenant_id, kind, payload)
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        for chat_id in chat_ids:
            response = await self._client.post(
                url,
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
            if response.status_code >= 400:
                detail = _redact(response.text, self._token)
                logger.error(
                    "Telegram sendMessage failed HTTP %s chat=%s: %s",
                    response.status_code,
                    chat_id,
                    detail,
                )
                response.raise_for_status()


def _redact(text: str, token: str) -> str:
    return text.replace(token, "[REDACTED]") if token else text
