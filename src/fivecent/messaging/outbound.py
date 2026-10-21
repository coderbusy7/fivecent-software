from __future__ import annotations

from uuid import uuid4

from fivecent.domain.events import Channel


class OutboundChannel:
    async def send(self, tenant_id: str, user_channel_id: str, text: str) -> str:
        raise NotImplementedError


class TestChannelSender(OutboundChannel):
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
