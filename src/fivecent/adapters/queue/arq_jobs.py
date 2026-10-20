from __future__ import annotations

from datetime import timedelta

from arq.connections import ArqRedis, RedisSettings
from arq.jobs import Retry

from fivecent.application.ports import LockBusy
from fivecent.application.turn import handle_turn
from fivecent.config import Settings, get_settings
from fivecent.domain.events import InboundMessageEvent


class ArqQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(self, event: InboundMessageEvent) -> None:
        await self._pool.enqueue_job("process_inbound", event.model_dump(mode="json"))


async def process_inbound(ctx: dict, payload: dict) -> None:
    event = InboundMessageEvent.model_validate(payload)
    try:
        await handle_turn(event, ctx["container"])
    except LockBusy as exc:
        raise Retry(defer=timedelta(seconds=2)) from exc


async def startup(ctx: dict) -> None:
    from fivecent.composition import build_container

    settings: Settings = ctx.get("settings") or get_settings()
    ctx["container"] = await build_container(settings, for_worker=True)


async def shutdown(ctx: dict) -> None:
    container = ctx.get("container")
    closer = getattr(container, "aclose", None)
    if closer:
        await closer()


def _redis_settings(url: str) -> RedisSettings:
    return RedisSettings.from_dsn(url)


def worker_settings_from(settings: Settings) -> type:
    redis_settings = _redis_settings(settings.redis_url)

    class WorkerSettings:
        functions = [process_inbound]
        on_startup = startup
        on_shutdown = shutdown
        redis_settings = redis_settings

    return WorkerSettings


class WorkerSettings:
    """`arq fivecent.adapters.queue.arq_jobs.WorkerSettings`"""

    functions = [process_inbound]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(
        get_settings().redis_url or "redis://localhost:6379/0"
    )
