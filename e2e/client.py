from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from e2e.config import E2EConfig


@dataclass
class TurnExchange:
    user_message: str
    reply: str
    status: str
    mode: str
    raw: dict[str, Any]
    elapsed_ms: int


class ChatClient:
    """Synchronous /chat client — best for asserting LLM replies."""

    def __init__(self, config: E2EConfig, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client or httpx.Client(timeout=180.0)
        self._owns = client is None

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def send(self, message: str, *, phone: str | None = None) -> TurnExchange:
        started = time.monotonic()
        response = self._client.post(
            f"{self.config.api_base.rstrip('/')}/chat",
            json={"phone": phone or self.config.phone, "message": message},
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        response.raise_for_status()
        body = response.json()
        return TurnExchange(
            user_message=message,
            reply=str(body.get("reply") or ""),
            status=str(body.get("status") or "ok"),
            mode="chat",
            raw=body if isinstance(body, dict) else {"body": body},
            elapsed_ms=elapsed_ms,
        )


class WhatsAppWebhookClient:
    """Signed Meta-style webhook POST — exercises real WhatsApp ingest + outbound."""

    def __init__(self, config: E2EConfig, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client or httpx.Client(timeout=180.0)
        self._owns = client is None

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def send(self, message: str, *, phone: str | None = None) -> TurnExchange:
        if not self.config.whatsapp_app_secret:
            raise RuntimeError(
                "WHATSAPP_APP_SECRET is required for webhook mode "
                "(set it in .env or the environment)."
            )
        from_phone = phone or self.config.phone
        payload = self._build_payload(message, from_phone)
        body = json.dumps(payload).encode()
        signature = (
            "sha256="
            + hmac.new(
                self.config.whatsapp_app_secret.encode(), body, hashlib.sha256
            ).hexdigest()
        )
        started = time.monotonic()
        response = self._client.post(
            f"{self.config.api_base.rstrip('/')}/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        # Prefer not to raise on 5xx so the runner can report the failure cleanly.
        try:
            raw = response.json()
        except ValueError:
            raw = {"text": response.text}
        if response.status_code >= 400:
            return TurnExchange(
                user_message=message,
                reply="",
                status=f"http_{response.status_code}",
                mode="webhook",
                raw=raw if isinstance(raw, dict) else {"body": raw},
                elapsed_ms=elapsed_ms,
            )
        return TurnExchange(
            user_message=message,
            reply="",  # Graph reply is async to the guest phone; not in HTTP body
            status=str((raw or {}).get("status") or "ok"),
            mode="webhook",
            raw=raw if isinstance(raw, dict) else {"body": raw},
            elapsed_ms=elapsed_ms,
        )

    def _build_payload(self, message: str, from_phone: str) -> dict[str, Any]:
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "e2e-waba",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15552014794",
                                    "phone_number_id": self.config.whatsapp_phone_number_id,
                                },
                                "contacts": [
                                    {
                                        "wa_id": from_phone,
                                        "profile": {"name": "E2E Test Guest"},
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": from_phone,
                                        "id": f"wamid.e2e.{uuid.uuid4().hex}",
                                        "timestamp": str(int(time.time())),
                                        "type": "text",
                                        "text": {"body": message},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }


def build_client(config: E2EConfig):
    if config.mode == "webhook":
        return WhatsAppWebhookClient(config)
    if config.mode == "chat":
        return ChatClient(config)
    raise ValueError(f"Unknown E2E mode: {config.mode!r} (use chat or webhook)")
