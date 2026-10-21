from __future__ import annotations

from typing import Callable, Optional, Protocol

from fivecent.application.registry import PluginRegistry
from fivecent.domain.events import InboundMessageEvent


class WebhookAdapter(Protocol):
    @property
    def configured(self) -> bool: ...

    def verify_signature(self, raw_body: bytes, signature: str) -> bool: ...

    def parse(
        self, payload: dict, tenant_for_account_id: Callable[[str], Optional[str]]
    ) -> list[InboundMessageEvent]: ...

    def verify_challenge(
        self, mode: str, token: str, challenge: str
    ) -> Optional[str]: ...


class WhatsAppWebhookAdapter:
    def __init__(self, app_secret: str, verify_token: str) -> None:
        self._app_secret = app_secret
        self._verify_token = verify_token

    @property
    def configured(self) -> bool:
        return bool(self._app_secret and self._verify_token)

    def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        from fivecent.adapters.inbound.whatsapp import verify_signature

        return verify_signature(self._app_secret, raw_body, signature)

    def parse(
        self, payload: dict, tenant_for_account_id: Callable[[str], Optional[str]]
    ) -> list[InboundMessageEvent]:
        from fivecent.adapters.inbound.whatsapp import normalize_whatsapp_payload

        return normalize_whatsapp_payload(
            payload,
            tenant_for_phone_number_id=tenant_for_account_id,
        )

    def verify_challenge(
        self, mode: str, token: str, challenge: str
    ) -> Optional[str]:
        from fivecent.adapters.inbound.whatsapp import verify_webhook_challenge

        return verify_webhook_challenge(
            mode, token, challenge, expected_token=self._verify_token
        )


def create_webhook_registry(app_secret: str, verify_token: str) -> PluginRegistry[WebhookAdapter]:
    return PluginRegistry({
        "whatsapp": WhatsAppWebhookAdapter(app_secret, verify_token),
    })
