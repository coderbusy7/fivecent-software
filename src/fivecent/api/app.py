from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from fivecent.adapters.inbound.http_chat import ChatInput, chat_to_event
from fivecent.adapters.inbound.whatsapp import (
    normalize_whatsapp_payload,
    verify_signature,
    verify_webhook_challenge,
)
from fivecent.adapters.persistence.engine import init_database, make_engine, make_sessionmaker
from fivecent.api.internal import register_internal_routes
from fivecent.application.ports import Container
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
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if container is not None:
            app.state.container = container
            app.state.settings = settings
            app.state.phone_map = phone_map or _phone_map(settings)
            yield
            return

        engine = make_engine(settings.database_url)
        sessions = make_sessionmaker(engine)
        await init_database(engine, sessions, settings)
        built = await build_container(settings, engine=engine, sessions=sessions)
        app.state.container = built
        app.state.settings = settings
        mapping = _phone_map(settings)
        tenant_id = await built.tenants.id_for_whatsapp_phone(settings.whatsapp_phone_number_id)
        if tenant_id:
            mapping[settings.whatsapp_phone_number_id] = tenant_id
        app.state.phone_map = mapping
        yield
        await built.aclose()
        await engine.dispose()

    app = FastAPI(title="Port Vauban Yacht Concierge", lifespan=lifespan)

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
    def health() -> dict[str, bool]:
        return {"ok": True}

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

    @app.post("/webhook")
    async def webhook(request: Request) -> dict[str, str]:
        current_settings: Settings = app.state.settings
        raw = await request.body()
        if not current_settings.whatsapp_app_secret:
            raise HTTPException(status_code=503, detail="App secret not configured.")
        header = request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(current_settings.whatsapp_app_secret, raw, header):
            raise HTTPException(status_code=403, detail="Invalid signature.")
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON.")
        routing = await app.state.container.tenants.whatsapp_routing_map()
        routing.update(app.state.phone_map)
        events = normalize_whatsapp_payload(
            payload,
            tenant_for_phone_number_id=lambda pid: routing.get(pid),
        )
        current: Container = app.state.container
        tenant_id = events[0].tenant_id if events else None
        await current.conversations.record_raw_inbound(
            tenant_id=tenant_id,
            channel="whatsapp",
            body=raw.decode("utf-8", errors="replace"),
        )
        for event in events:
            await current.queue.enqueue(event)
        return {"status": "ok"}

    @app.get("/webhook", response_class=PlainTextResponse)
    def verify_webhook(
        mode: str = Query(alias="hub.mode"),
        verify_token: str = Query(alias="hub.verify_token"),
        challenge: str = Query(alias="hub.challenge"),
    ) -> str:
        expected = app.state.settings.whatsapp_verify_token
        if not expected:
            raise HTTPException(status_code=503, detail="Verification token not configured.")
        result = verify_webhook_challenge(mode, verify_token, challenge, expected)
        if result is None:
            raise HTTPException(status_code=403, detail="Verification failed.")
        return result

    register_internal_routes(
        app,
        get_container=lambda: app.state.container,
        get_settings_fn=lambda: app.state.settings,
    )
    return app


app = create_app()
