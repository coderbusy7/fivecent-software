"""Production startup guards and dependency readiness checks."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.config import Settings


class ProductionConfigError(RuntimeError):
    """Raised when APP_ENV=production settings are unsafe for launch."""


def validate_production_settings(settings: Settings) -> None:
    if not settings.is_production:
        return
    errors: list[str] = []
    db = (settings.database_url or "").lower()
    if not db or "sqlite" in db:
        errors.append("DATABASE_URL must be managed Postgres (sqlite is not allowed).")
    if not (settings.redis_url or "").strip():
        errors.append("REDIS_URL is required for cache/locks/queue coordination.")
    if (settings.queue_backend or "").strip().lower() != "arq":
        errors.append("QUEUE_BACKEND must be arq so webhooks ACK without inline bot work.")
    if not (settings.admin_api_token or "").strip():
        errors.append("ADMIN_API_TOKEN is required.")
    if not (settings.whatsapp_verify_token or "").strip():
        errors.append("WHATSAPP_VERIFY_TOKEN is required.")
    if not (settings.whatsapp_app_secret or "").strip():
        errors.append("WHATSAPP_APP_SECRET is required.")
    if not (settings.whatsapp_access_token or "").strip():
        errors.append("WHATSAPP_ACCESS_TOKEN is required.")
    phone = (settings.whatsapp_phone_number_id or "").strip()
    if not phone or phone == "seed-phone-number-id":
        errors.append("WHATSAPP_PHONE_NUMBER_ID must be a real Meta phone number id.")
    if (settings.telegram_bot_token or "").strip() and not (
        settings.telegram_webhook_secret or ""
    ).strip():
        errors.append("TELEGRAM_WEBHOOK_SECRET is required when Telegram is enabled.")
    if settings.enable_test_chat and not (settings.admin_api_token or "").strip():
        errors.append("ENABLE_TEST_CHAT requires ADMIN_API_TOKEN.")
    if errors:
        raise ProductionConfigError(
            "Unsafe production configuration:\n- " + "\n- ".join(errors)
        )


async def check_database(sessions: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    try:
        async with sessions() as session:
            await session.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


async def check_redis(redis_url: str) -> dict[str, Any]:
    if not (redis_url or "").strip():
        return {"ok": False, "error": "not_configured"}
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            pong = await client.ping()
            return {"ok": bool(pong)}
        finally:
            await client.aclose()
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


async def readiness_report(
    settings: Settings,
    *,
    sessions: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "app_env": settings.app_env,
        "queue": (
            "arq"
            if settings.redis_url and settings.queue_backend == "arq"
            else "inline"
        ),
    }
    if sessions is not None:
        checks["database"] = await check_database(sessions)
    else:
        checks["database"] = {"ok": False, "error": "unavailable"}
    checks["redis"] = await check_redis(settings.redis_url)
    if settings.is_production:
        ready = bool(checks["database"].get("ok")) and bool(checks["redis"].get("ok"))
    else:
        ready = bool(checks["database"].get("ok"))
    return {"ready": ready, "checks": checks}
