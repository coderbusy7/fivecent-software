from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def format_telegram_alert(tenant_id: str, kind: str, payload: dict) -> str:
    agency = str(payload.get("agency_name") or tenant_id)
    lines = [f"Fivecent · {kind.upper()} — {agency}", "────────────────────────"]

    if kind == "hold":
        lines.append("NEW 24h SOFT HOLD")
        name = str(payload.get("customer_name") or "").strip()
        email = str(payload.get("email") or "").strip()
        if name or email:
            lines.append(f"Client: {name or 'Guest'}" + (f" ({email})" if email else ""))
        lines.append(
            f"Yacht: {payload.get('boat_name') or payload.get('boat_id') or '—'}"
        )
        lines.append(
            f"Date: {payload.get('start_date')} · {payload.get('days')} day(s)"
        )
        if payload.get("quote_total"):
            currency = payload.get("currency") or "EUR"
            lines.append(f"Quote: {payload['quote_total']} {currency}")
        if payload.get("expires_at"):
            lines.append(f"Expires: {payload['expires_at']}")
        lines.append("Contract unconfirmed — action required.")
    elif kind == "followup":
        lines.append("BROKER FOLLOW-UP REQUEST")
        name = str(payload.get("customer_name") or "").strip()
        if name:
            lines.append(f"Guest: {name}")
        lines.append(f"Destination: {payload.get('destination') or '—'}")
        pref = payload.get("contact_preference")
        if pref:
            lines.append(f"Contact: {pref}")
        notes = str(payload.get("notes") or "").strip()
        if notes:
            lines.append(f"Notes: {notes}")
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
        lines.append(f"Quote id: {payload['quote_id']}")

    wa_me = payload.get("wa_me")
    mute = payload.get("mute_command")
    lines.append("")
    if wa_me:
        lines.append(f"Open WhatsApp: {wa_me}")
    elif payload.get("guest_user_id"):
        lines.append(f"Guest id: {payload['guest_user_id']}")
    if mute:
        lines.append(f"Mute bot 24h: {mute}")
    return "\n".join(lines)


def telegram_chat_ids(payload: dict) -> list[str]:
    ids: list[str] = []
    desk = payload.get("tenant_telegram_chat_id")
    if desk is not None:
        text = str(desk).strip()
        if text:
            ids.append(text)
            return ids
    brokers = payload.get("alert_brokers") or []
    if not isinstance(brokers, list):
        return []
    for broker in brokers:
        if not isinstance(broker, dict):
            continue
        chat_id = broker.get("telegram_chat_id")
        if chat_id is None:
            continue
        text = str(chat_id).strip()
        if text and text not in ids:
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
