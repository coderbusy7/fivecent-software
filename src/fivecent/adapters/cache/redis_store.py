from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Optional
from uuid import uuid4

from redis.asyncio import Redis

from fivecent.application.ports import LockBusy

WINDOW_TTL_SECONDS = 48 * 3600
WINDOW_LIMIT = 15
LOCK_TTL_SECONDS = 120
LOCK_RENEW_SECONDS = 60
DEBOUNCE_TTL_SECONDS = 60
RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""
DRAIN_BUFFER = """
local items = redis.call("lrange", KEYS[1], 0, -1)
redis.call("del", KEYS[1])
return items
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
        renew_task = None
        try:
            renew_task = asyncio.create_task(self._renew_lock(key, token))
            yield
        finally:
            if renew_task is not None:
                renew_task.cancel()
            await self._redis.eval(RELEASE_LOCK, 1, key, token)

    async def _renew_lock(self, key: str, token: str) -> None:
        """Keep the lease alive for long LLM turns (ED-12)."""
        while True:
            await asyncio.sleep(LOCK_RENEW_SECONDS)
            current = await self._redis.get(key)
            if current != token:
                return
            await self._redis.expire(key, LOCK_TTL_SECONDS)

    async def is_human_active(self, tenant_id: str, channel: str, user_channel_id: str) -> bool:
        return bool(await self._redis.exists(f"human:{tenant_id}:{channel}:{user_channel_id}"))

    async def set_human_active(
        self, tenant_id: str, channel: str, user_channel_id: str, ttl_seconds: int = 86400
    ) -> None:
        await self._redis.set(
            f"human:{tenant_id}:{channel}:{user_channel_id}", "1", ex=ttl_seconds
        )

    async def clear_human_active(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> None:
        await self._redis.delete(f"human:{tenant_id}:{channel}:{user_channel_id}")

    async def check_tenant_rate_limit(
        self, tenant_id: str, *, limit_per_minute: int = 60
    ) -> bool:
        """Return True when under budget; False when tenant is rate-limited."""
        key = f"rate:tenant:{tenant_id}:minute"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, 60)
        return int(count) <= limit_per_minute

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

    def _buf_key(self, tenant_id: str, channel: str, user_channel_id: str) -> str:
        return f"debounce:buf:{tenant_id}:{channel}:{user_channel_id}"

    def _gen_key(self, tenant_id: str, channel: str, user_channel_id: str) -> str:
        return f"debounce:gen:{tenant_id}:{channel}:{user_channel_id}"

    async def append_debounce_fragment(
        self,
        tenant_id: str,
        channel: str,
        user_channel_id: str,
        *,
        text: str,
        provider_message_id: str,
    ) -> None:
        key = self._buf_key(tenant_id, channel, user_channel_id)
        payload = json.dumps(
            {"text": text, "provider_message_id": provider_message_id},
            separators=(",", ":"),
        )
        pipe = self._redis.pipeline()
        pipe.rpush(key, payload)
        pipe.expire(key, DEBOUNCE_TTL_SECONDS)
        await pipe.execute()

    async def bump_debounce_generation(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> int:
        key = self._gen_key(tenant_id, channel, user_channel_id)
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, DEBOUNCE_TTL_SECONDS)
        results = await pipe.execute()
        return int(results[0])

    async def get_debounce_generation(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> int:
        raw = await self._redis.get(self._gen_key(tenant_id, channel, user_channel_id))
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    async def drain_debounce_fragments(
        self, tenant_id: str, channel: str, user_channel_id: str
    ) -> list[dict]:
        key = self._buf_key(tenant_id, channel, user_channel_id)
        raw_items = await self._redis.eval(DRAIN_BUFFER, 1, key)
        if not raw_items:
            return []
        seen: set[str] = set()
        out: list[dict] = []
        for raw in raw_items:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                item = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            mid = str(item.get("provider_message_id") or "")
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            out.append(item)
        return out
