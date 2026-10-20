from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fivecent.domain.events import InboundMessageEvent

VerifyTokenLookup = Callable[[], Optional[str]]
TenantLookup = Callable[[str], Optional[str]]


def verify_webhook_challenge(mode: str, token: str, challenge: str, expected_token: str) -> Optional[str]:
    if mode == "subscribe" and expected_token and hmac.compare_digest(token, expected_token):
        return challenge
    return None


def verify_signature(app_secret: str, raw_body: bytes, header: str) -> bool:
    if not app_secret or not header:
        return False
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, header)


def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def normalize_whatsapp_payload(
    payload: dict,
    *,
    tenant_for_phone_number_id: TenantLookup,
) -> list[InboundMessageEvent]:
    events: list[InboundMessageEvent] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            phone_number_id = metadata.get("phone_number_id")
            if not phone_number_id:
                continue
            tenant_id = tenant_for_phone_number_id(str(phone_number_id))
            if not tenant_id:
                continue
            for message in value.get("messages") or []:
                if message.get("type") != "text":
                    continue
                text = ((message.get("text") or {}).get("body")) or None
                events.append(
                    InboundMessageEvent(
                        tenant_id=tenant_id,
                        channel="whatsapp",
                        user_channel_id=str(message.get("from", "")),
                        provider_message_id=str(message.get("id", "")),
                        text=text,
                        timestamp=_ts(message.get("timestamp")),
                    )
                )
            for echo in value.get("smb_message_echoes") or []:
                text = ((echo.get("text") or {}).get("body")) or None
                user_id = str(echo.get("to") or echo.get("recipient") or "")
                if not user_id or not echo.get("id"):
                    continue
                events.append(
                    InboundMessageEvent(
                        tenant_id=tenant_id,
                        channel="whatsapp",
                        user_channel_id=user_id,
                        provider_message_id=str(echo["id"]),
                        text=text,
                        timestamp=_ts(echo.get("timestamp")),
                        is_business_echo=True,
                    )
                )
    return [event for event in events if event.provider_message_id and event.user_channel_id]
