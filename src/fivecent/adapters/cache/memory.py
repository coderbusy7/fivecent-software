from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from time import time

WINDOW_TTL_SECONDS = 48 * 3600
WINDOW_LIMIT = 15


class MemoryCache:
    def __init__(self) -> None:
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._human: dict[tuple[str, str, str], float] = {}
        self._window: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}
        self._outbound: dict[tuple[str, str], float] = {}
        self._counters: dict[str, int] = defaultdict(int)

    @asynccontextmanager
    async def lock(self, tenant_id: str, channel: str, user_channel_id: str):
        async with self._locks[(tenant_id, channel, user_channel_id)]:
            yield

    async def is_human_active(self, tenant_id: str, channel: str, user_channel_id: str) -> bool:
        expires = self._human.get((tenant_id, channel, user_channel_id))
        return bool(expires and expires > time())

    async def set_human_active(
        self, tenant_id: str, channel: str, user_channel_id: str, ttl_seconds: int = 86400
    ) -> None:
        self._human[(tenant_id, channel, user_channel_id)] = time() + ttl_seconds

    async def get_window(self, tenant_id: str, channel: str, user_channel_id: str) -> list[dict]:
        packed = self._window.get((tenant_id, channel, user_channel_id))
        if not packed or packed[0] < time():
            return []
        return list(packed[1])

    async def replace_window(
        self, tenant_id: str, channel: str, user_channel_id: str, turns: list[dict]
    ) -> None:
        self._window[(tenant_id, channel, user_channel_id)] = (
            time() + WINDOW_TTL_SECONDS,
            turns[-WINDOW_LIMIT:],
        )

    async def remember_outbound(self, tenant_id: str, provider_message_id: str) -> None:
        self._outbound[(tenant_id, provider_message_id)] = time() + 2 * 86400

    async def was_sent_by_bot(self, tenant_id: str, provider_message_id: str) -> bool:
        expires = self._outbound.get((tenant_id, provider_message_id))
        return bool(expires and expires > time())

    async def incr(self, key: str) -> int:
        self._counters[key] += 1
        return self._counters[key]
