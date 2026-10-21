from __future__ import annotations

import logging
from uuid import uuid4

import httpx

from fivecent.messaging.errors import SessionWindowClosedError, graph_error_code
from fivecent.messaging.outbound import OutboundChannel

logger = logging.getLogger(__name__)


class WhatsAppSender(OutboundChannel):
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

    async def send(self, tenant_id: str, user_channel_id: str, text: str) -> str:
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
            detail = response.text.replace(self._token, "[REDACTED]").replace(
                user_channel_id, "[PHONE]"
            )
            logger.error("WhatsApp API rejected reply (HTTP %s): %s", response.status_code, detail)
            try:
                body = response.json()
            except ValueError:
                body = None
            if graph_error_code(body) == SessionWindowClosedError.code:
                raise SessionWindowClosedError(
                    tenant_id=tenant_id,
                    user_channel_id=user_channel_id,
                    detail=detail,
                )
            response.raise_for_status()
        payload = response.json()
        messages = payload.get("messages") or []
        if messages and messages[0].get("id"):
            return str(messages[0]["id"])
        return f"wa-local-{uuid4()}"
