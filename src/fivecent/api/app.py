from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from fivecent.adapters.inbound.http_chat import ChatInput, chat_to_event
from fivecent.messaging.factory import build_inbound_plugins
from fivecent.messaging.plugin_whatsapp.callback import sync_waba_webhook_override
from fivecent.messaging.graph import WebhookAdapter
from fivecent.adapters.persistence.engine import init_database, make_engine, make_sessionmaker
from fivecent.api.internal import register_internal_routes
from fivecent.application.ports import Container
from fivecent.application.registry import PluginRegistry
from fivecent.application.turn import handle_turn
from fivecent.composition import build_container
from fivecent.config import Settings, get_settings

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
            app.state.phone_map = (
                phone_map if phone_map is not None else _phone_map(settings)
            )
            app.state.webhook_adapters = webhook_adapters
            yield
            return

        engine = make_engine(settings.database_url)
        sessions = make_sessionmaker(engine)
        await init_database(engine, sessions, settings)
        built = await build_container(settings, engine=engine, sessions=sessions)
        app.state.container = built
        app.state.settings = settings
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
            "chat": "POST /chat",
            "webhook": "/webhook",
            "admin": "Streamlit on ADMIN_PORT, not this API",
        }

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
        }

    @app.post("/chat")
    async def chat(payload: ChatInput) -> dict[str, str]:
        current: Container = app.state.container
        event = chat_to_event(payload, current.seed_tenant_id)
        result = await handle_turn(event, current)
        if result.skipped and result.reason == "hitl":
            return {"reply": "", "status": "human_active"}
        if result.skipped:
            return {"reply": "", "status": result.reason or "skipped"}
        return {"reply": result.reply or ""}

    async def process_webhook_payload(
        channel: str, payload: dict, raw: bytes
    ) -> None:
        tenant_id: Optional[str] = None
        try:
            routing = await app.state.container.tenants.channel_routing_map(channel)
            if channel == "whatsapp":
                routing.update(app.state.phone_map)
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
            current: Container = app.state.container
            try:
                await current.conversations.record_raw_inbound(
                    tenant_id=tenant_id,
                    channel=channel,
                    body=raw.decode("utf-8", errors="replace"),
                )
            except Exception:
                logger.exception("failed to store inbound webhook channel=%s", channel)
            for event in events:
                await current.queue.enqueue(event)
        except Exception:
            logger.exception("webhook processing failed channel=%s tenant=%s", channel, tenant_id)

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
        # Inline so Render free instances cannot drop the Graph reply after 200.
        await process_webhook_payload(channel, payload, raw)
        return {"status": "ok"}

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
    return app


app = create_app()
