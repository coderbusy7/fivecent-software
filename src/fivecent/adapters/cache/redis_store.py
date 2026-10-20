from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Optional
from uuid import uuid4

from redis.asyncio import Redis

from fivecent.application.ports import LockBusy

WINDOW_TTL_SECONDS = 48 * 3600
WINDOW_LIMIT = 15
LOCK_TTL_SECONDS = 45
RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""


class RedisCache:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @asynccontextmanager
    async def lock(self, tenant_id: str, channel: str, user_channel_id: str):
        key = f"lock:{tenant_id}:{channel}:{user_channel_id}"
        token = str(uuid4())
        acquired = await self._redis.set(key, token, nx=True, ex=LOCK_TTL_SECONDS)
        if not acquired:
            raise LockBusy(key)
        try:
            yield
        finally:
            await self._redis.eval(RELEASE_LOCK, 1, key, token)

    async def is_human_active(self, tenant_id: str, channel: str, user_channel_id: str) -> bool:
        return bool(await self._redis.exists(f"human:{tenant_id}:{channel}:{user_channel_id}"))

    async def set_human_active(
        self, tenant_id: str, channel: str, user_channel_id: str, ttl_seconds: int = 86400
    ) -> None:
        await self._redis.set(
            f"human:{tenant_id}:{channel}:{user_channel_id}", "1", ex=ttl_seconds
        )

    async def get_window(self, tenant_id: str, channel: str, user_channel_id: str) -> list[dict]:
        raw: Optional[str] = await self._redis.get(f"window:{tenant_id}:{channel}:{user_channel_id}")
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []

    async def replace_window(
        self, tenant_id: str, channel: str, user_channel_id: str, turns: list[dict]
    ) -> None:
        await self._redis.set(
            f"window:{tenant_id}:{channel}:{user_channel_id}",
            json.dumps(turns[-WINDOW_LIMIT:]),
            ex=WINDOW_TTL_SECONDS,
        )

    async def remember_outbound(self, tenant_id: str, provider_message_id: str) -> None:
        await self._redis.set(f"out:{tenant_id}:{provider_message_id}", "1", ex=2 * 86400)

    async def was_sent_by_bot(self, tenant_id: str, provider_message_id: str) -> bool:
        return bool(await self._redis.exists(f"out:{tenant_id}:{provider_message_id}"))

    async def incr(self, key: str) -> int:
        return int(await self._redis.incr(key))
