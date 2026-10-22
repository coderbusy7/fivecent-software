from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from fivecent.adapters.inbound.http_chat import ChatInput, chat_to_event
from fivecent.messaging.factory import build_inbound_plugins
from fivecent.messaging.plugin_whatsapp.callback import sync_waba_webhook_override
from fivecent.messaging.graph import WebhookAdapter
from fivecent.adapters.persistence.engine import init_database, make_engine, make_sessionmaker
from fivecent.api.chat_guard import enforce_chat_access
from fivecent.api.internal import register_internal_routes
from fivecent.application.ports import Container
from fivecent.application.registry import PluginRegistry
from fivecent.application.turn import handle_turn
from fivecent.composition import build_container
from fivecent.config import Settings, get_settings
from fivecent.messaging.plugin_telegram import register_telegram_routes
from fivecent.production import readiness_report, validate_production_settings

logger = logging.getLogger(__name__)


def _phone_map(settings: Settings) -> dict[str, str]:
    if not settings.whatsapp_phone_number_id:
        return {}
    return {settings.whatsapp_phone_number_id: settings.seed_tenant_id}


def create_app(
    *,
    container: Optional[Container] = None,
    settings: Optional[Settings] = None,
    phone_map: Optional[dict[str, str]] = None,
    webhook_adapters: Optional[PluginRegistry[WebhookAdapter]] = None,
) -> FastAPI:
    settings = settings or get_settings()
    webhook_adapters = webhook_adapters or build_inbound_plugins(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if container is not None:
            app.state.container = container
            app.state.settings = settings
            app.state.sessions = getattr(container.tenants, "_sessions", None)
            app.state.phone_map = (
                phone_map if phone_map is not None else _phone_map(settings)
            )
            app.state.webhook_adapters = webhook_adapters
            yield
            return

        validate_production_settings(settings)
        engine = make_engine(settings.database_url)
        sessions = make_sessionmaker(engine)
        await init_database(engine, sessions, settings)
        built = await build_container(settings, engine=engine, sessions=sessions)
        app.state.container = built
        app.state.settings = settings
        app.state.sessions = sessions
        app.state.webhook_adapters = webhook_adapters
        mapping = _phone_map(settings)
        mapping.update(await built.tenants.channel_routing_map("whatsapp"))
        app.state.phone_map = mapping
        override_task = asyncio.create_task(sync_waba_webhook_override(settings))
        yield
        override_task.cancel()
        await built.aclose()
        await engine.dispose()

    app = FastAPI(title="Fivecent", lifespan=lifespan)

    @app.get("/")
    def root() -> dict[str, object]:
        return {
            "ok": True,
            "service": "fivecent",
            "health": "/health",
            "live": "/live",
            "ready": "/ready",
            "chat": "POST /chat",
            "webhook": "/webhook",
            "admin": "Streamlit on ADMIN_PORT, not this API",
        }

    @app.get("/live")
    def live() -> dict[str, object]:
        """Liveness: process is up. Prefer this for orchestrator restart probes."""
        return {"ok": True}

    @app.get("/ready")
    async def ready() -> dict[str, object]:
        """Readiness: dependencies that must work before taking traffic."""
        current: Settings = getattr(app.state, "settings", settings)
        report = await readiness_report(
            current, sessions=getattr(app.state, "sessions", None)
        )
        if not report["ready"]:
            raise HTTPException(status_code=503, detail=report)
        return report

    @app.get("/health")
    def health() -> dict[str, object]:
        current: Settings = getattr(app.state, "settings", settings)
        phone = current.whatsapp_phone_number_id
        return {
            "ok": True,
            "openai": bool(current.openai_api_key),
            "anthropic": bool(current.anthropic_api_key),
            "llm_providers": list(app.state.container.providers.keys)
            if hasattr(app.state, "container")
            else [],
            "redis": bool(current.redis_url),
            "queue": (
                "arq"
                if current.redis_url and current.queue_backend == "arq"
                else "inline"
            ),
            "whatsapp": {
                "verify_token": bool(current.whatsapp_verify_token),
                "app_secret": bool(current.whatsapp_app_secret),
                "access_token": bool(current.whatsapp_access_token),
                "phone_number_id": bool(phone and phone != "seed-phone-number-id"),
            },
            "telegram": {
                "bot_token": bool(current.telegram_bot_token),
                "bot_username": current.telegram_bot_username or "fivecent_test_bot",
                "webhook_secret": bool(current.telegram_webhook_secret),
            },
            "app_env": current.app_env,
            "test_chat": current.chat_enabled(),
            "probes": {"live": "/live", "ready": "/ready"},
        }

    @app.post("/chat")
    async def chat(
        payload: ChatInput,
        request: Request,
        authorization: str = Header(default=""),
    ) -> dict[str, str]:
        current_settings: Settings = getattr(app.state, "settings", settings)
        enforce_chat_access(current_settings, request, authorization)
        current: Container = app.state.container
        event = chat_to_event(payload, current.seed_tenant_id)
        result = await handle_turn(event, current)
        if result.skipped and result.reason == "hitl":
            return {"reply": "", "status": "human_active"}
        if result.skipped:
            return {"reply": "", "status": result.reason or "skipped"}
        return {"reply": result.reply or ""}

    async def _routing_for_channel(channel: str) -> dict[str, str]:
        routing = dict(await app.state.container.tenants.channel_routing_map(channel))
        # Startup seed map fills gaps only — DB mappings win for multi-agency.
        for phone_id, tenant_id in (getattr(app.state, "phone_map", {}) or {}).items():
            routing.setdefault(phone_id, tenant_id)
        return routing

    async def process_webhook_payload(
        channel: str, payload: dict, raw: bytes, *, payload_id: Optional[str] = None
    ) -> str:
        """Persist then enqueue. Raises on failure so callers can return non-200."""
        current: Container = app.state.container
        routing = await _routing_for_channel(channel)
        events = app.state.webhook_adapters.get(channel).parse(
            payload, tenant_for_account_id=routing.get
        )
        tenant_id = events[0].tenant_id if events else None
        phone_number_id = (
            ((payload.get("entry") or [{}])[0].get("changes") or [{}])[0]
            .get("value", {})
            .get("metadata", {})
            .get("phone_number_id")
        )
        logger.warning(
            "webhook accepted channel=%s events=%s tenant=%s phone_number_id=%s",
            channel,
            len(events),
            tenant_id,
            phone_number_id,
        )
        if payload_id is None:
            payload_id = await current.conversations.record_raw_inbound(
                tenant_id=tenant_id,
                channel=channel,
                body=raw.decode("utf-8", errors="replace"),
            )
        try:
            for event in events:
                await current.queue.enqueue(event)
        except Exception as exc:
            await current.conversations.mark_raw_inbound(
                payload_id, status="failed", error=str(exc)[:2000]
            )
            logger.exception(
                "webhook enqueue failed channel=%s tenant=%s payload=%s",
                channel,
                tenant_id,
                payload_id,
            )
            raise
        return payload_id

    app.state.process_webhook_payload = process_webhook_payload  # type: ignore[attr-defined]

    async def receive_webhook(channel: str, request: Request) -> dict[str, str]:
        try:
            adapter = app.state.webhook_adapters.get(channel)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Channel webhook is not configured.") from exc
        if not adapter.configured:
            raise HTTPException(status_code=503, detail="Channel webhook credentials are not configured.")
        raw = await request.body()
        header = request.headers.get("X-Hub-Signature-256", "")
        logger.warning(
            "webhook POST channel=%s bytes=%s signature=%s",
            channel,
            len(raw),
            "present" if header else "missing",
        )
        if not adapter.verify_signature(raw, header):
            logger.warning("webhook rejected invalid signature channel=%s", channel)
            raise HTTPException(status_code=403, detail="Invalid signature.")
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON.")
        # Success only after durable store + enqueue. Meta retries on 5xx.
        try:
            payload_id = await process_webhook_payload(channel, payload, raw)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail="Webhook accepted but processing failed."
            ) from exc
        return {"status": "ok", "payload_id": payload_id}

    @app.post("/webhook")
    async def whatsapp_webhook(request: Request) -> dict[str, str]:
        return await receive_webhook("whatsapp", request)

    @app.post("/webhooks/{channel}")
    async def channel_webhook(channel: str, request: Request) -> dict[str, str]:
        return await receive_webhook(channel, request)

    def verify_channel_webhook(
        channel: str,
        mode: str = Query(alias="hub.mode"),
        verify_token: str = Query(alias="hub.verify_token"),
        challenge: str = Query(alias="hub.challenge"),
    ) -> str:
        try:
            adapter = app.state.webhook_adapters.get(channel)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Channel webhook is not configured.") from exc
        if not adapter.configured:
            raise HTTPException(status_code=503, detail="Channel webhook credentials are not configured.")
        result = adapter.verify_challenge(mode, verify_token, challenge)
        if result is None:
            raise HTTPException(status_code=403, detail="Verification failed.")
        return result

    @app.get("/webhook", response_class=PlainTextResponse)
    def verify_whatsapp_webhook(
        mode: str = Query(alias="hub.mode"),
        verify_token: str = Query(alias="hub.verify_token"),
        challenge: str = Query(alias="hub.challenge"),
    ) -> str:
        return verify_channel_webhook("whatsapp", mode, verify_token, challenge)

    @app.get("/webhooks/{channel}", response_class=PlainTextResponse)
    def verify_channel(
        channel: str,
        mode: str = Query(alias="hub.mode"),
        verify_token: str = Query(alias="hub.verify_token"),
        challenge: str = Query(alias="hub.challenge"),
    ) -> str:
        return verify_channel_webhook(channel, mode, verify_token, challenge)

    register_internal_routes(
        app,
        get_container=lambda: app.state.container,
        get_settings_fn=lambda: app.state.settings,
    )
    register_telegram_routes(
        app,
        get_container=lambda: app.state.container,
        get_settings_fn=lambda: app.state.settings,
    )
    return app


app = create_app()
