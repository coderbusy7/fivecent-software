"""Shared Meta Graph webhook behaviour. Channel plugins inherit this."""

from __future__ import annotations

import hashlib
import hmac
from typing import Callable, Optional, Protocol

from fivecent.domain.events import InboundMessageEvent

TenantLookup = Callable[[str], Optional[str]]


class WebhookAdapter(Protocol):
    @property
    def configured(self) -> bool: ...

    def verify_signature(self, raw_body: bytes, signature: str) -> bool: ...

    def parse(
        self, payload: dict, tenant_for_account_id: TenantLookup
    ) -> list[InboundMessageEvent]: ...

    def verify_challenge(
        self, mode: str, token: str, challenge: str
    ) -> Optional[str]: ...


def verify_webhook_challenge(
    mode: str, token: str, challenge: str, expected_token: str
) -> Optional[str]:
    if mode == "subscribe" and expected_token and hmac.compare_digest(token, expected_token):
        return challenge
    return None


def verify_signature(app_secret: str, raw_body: bytes, header: str) -> bool:
    if not app_secret or not header:
        return False
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, header)


class GraphWebhookAdapter:
    """HMAC + hub.challenge for WhatsApp, Instagram, and Messenger."""

    channel: str = ""

    def __init__(self, app_secret: str = "", verify_token: str = "") -> None:
        self._app_secret = app_secret
        self._verify_token = verify_token

    @property
    def configured(self) -> bool:
        return bool(self._app_secret and self._verify_token)

    def verify_signature(self, raw_body: bytes, header: str) -> bool:
        return verify_signature(self._app_secret, raw_body, header)

    def verify_challenge(self, mode: str, token: str, challenge: str) -> Optional[str]:
        return verify_webhook_challenge(mode, token, challenge, self._verify_token)

    def parse(
        self, payload: dict, tenant_for_account_id: TenantLookup
    ) -> list[InboundMessageEvent]:
        raise NotImplementedError(f"{self.channel or 'graph'} inbound parse is not implemented.")
