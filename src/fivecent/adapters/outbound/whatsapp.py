from __future__ import annotations

import json
import logging
from uuid import uuid4
from typing import Protocol

import httpx

from fivecent.domain.events import Channel

logger = logging.getLogger(__name__)


class WhatsAppSender:
    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        graph_version: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = access_token
        self._phone_number_id = phone_number_id
        self._graph_version = graph_version
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_whatsapp(self, user_channel_id: str, text: str) -> str:
        if not self._token or not self._phone_number_id:
            raise RuntimeError("WhatsApp outbound is not configured.")
        url = (
            f"https://graph.facebook.com/{self._graph_version}/"
            f"{self._phone_number_id}/messages"
        )
        response = await self._client.post(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": user_channel_id,
                "type": "text",
                "text": {"body": text},
            },
        )
        if response.status_code >= 400:
            detail = response.text.replace(self._token, "[REDACTED]").replace(user_channel_id, "[PHONE]")
            logger.error("WhatsApp API rejected reply (HTTP %s): %s", response.status_code, detail)
            response.raise_for_status()
        payload = response.json()
        messages = payload.get("messages") or []
        if messages and messages[0].get("id"):
            return str(messages[0]["id"])
        return f"wa-local-{uuid4()}"

    async def send(self, tenant_id: str, user_channel_id: str, text: str) -> str:
        return await self.send_whatsapp(user_channel_id, text)


class OutboundChannel(Protocol):
    async def send(self, tenant_id: str, user_channel_id: str, text: str) -> str: ...


class TestChannelSender:
    async def send(self, tenant_id: str, user_channel_id: str, text: str) -> str:
        return f"test-out-{uuid4()}"


class ChannelSenderRegistry:
    def __init__(self, channels: dict[str, OutboundChannel]) -> None:
        self._channels = dict(channels)

    async def send(
        self, tenant_id: str, channel: Channel, user_channel_id: str, text: str
    ) -> str:
        sender = self._channels.get(channel)
        if sender is None:
            raise ValueError(f"Outbound channel '{channel}' is not configured.")
        return await sender.send(tenant_id, user_channel_id, text)


class LogBrokerAlerts:
    async def notify_hold(self, tenant_id: str, payload: dict) -> None:
        logger.info("broker hold alert tenant=%s payload=%s", tenant_id, json.dumps(payload))
