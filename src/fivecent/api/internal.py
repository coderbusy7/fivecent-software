from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from fivecent.adapters.persistence.repos import SqlConversationRepo
from fivecent.adapters.persistence.tenants import SqlTenantRepo
from fivecent.application.ports import Container
from fivecent.config import Settings
from fivecent.domain.tenancy import (
    TenantConfigError,
    validate_feature_flags,
    validate_rate,
    validate_routing_mode,
)


class TenantCreate(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    agency_name: str
    home_port: str
    whatsapp_phone_number_id: str
    whatsapp_waba_id: str = ""
    currency: str = "EUR"
    vat_rate: Decimal = Decimal("0.20")
    apa_rate: Decimal = Decimal("0.30")
    max_discount_pct: Decimal = Decimal("0.08")
    service_type: str = Field(default="yachting", min_length=1, max_length=64)
    llm_provider: str = Field(default="openai", min_length=1, max_length=64)
    llm_model: str = Field(default="gpt-4o-mini", min_length=1, max_length=128)
    routing_mode: str = "broadcast"
    feature_flags: Optional[dict] = None

    @field_validator("vat_rate", "apa_rate", "max_discount_pct", mode="before")
    @classmethod
    def _rate(cls, value: object) -> Decimal:
        return validate_rate("rate", Decimal(str(value)))

    @field_validator("routing_mode")
    @classmethod
    def _routing(cls, value: str) -> str:
        return validate_routing_mode(value)

    @field_validator("feature_flags")
    @classmethod
    def _flags(cls, value: Optional[dict]) -> Optional[dict]:
        if value is None:
            return None
        return validate_feature_flags(value)


class TenantPatch(BaseModel):
    agency_name: Optional[str] = None
    home_port: Optional[str] = None
    currency: Optional[str] = None
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_waba_id: Optional[str] = None
    vat_rate: Optional[Decimal] = None
    apa_rate: Optional[Decimal] = None
    max_discount_pct: Optional[Decimal] = None
    service_type: Optional[str] = Field(default=None, min_length=1, max_length=64)
    llm_provider: Optional[str] = Field(default=None, min_length=1, max_length=64)
    llm_model: Optional[str] = Field(default=None, min_length=1, max_length=128)
    routing_mode: Optional[str] = None
    feature_flags: Optional[dict] = None
    telegram_chat_id: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("vat_rate", "apa_rate", "max_discount_pct", mode="before")
    @classmethod
    def _rate(cls, value: object) -> Optional[Decimal]:
        if value is None:
            return None
        return validate_rate("rate", Decimal(str(value)))

    @field_validator("routing_mode")
    @classmethod
    def _routing(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_routing_mode(value)

    @field_validator("feature_flags")
    @classmethod
    def _flags(cls, value: Optional[dict]) -> Optional[dict]:
        if value is None:
            return None
        return validate_feature_flags(value)


class BrokerCreate(BaseModel):
    name: str
    phone_number: str
    telegram_chat_id: Optional[str] = None
    role: str = "broker"


class BrokerPatch(BaseModel):
    is_active: Optional[bool] = None
    telegram_chat_id: Optional[str] = None


class WhatsAppTestSend(BaseModel):
    phone: str = Field(min_length=8, max_length=32)
    message: str = Field(
        default="Fivecent test: Elena is online. Reply here to continue.",
        min_length=1,
        max_length=1000,
    )
    tenant_id: Optional[str] = None


def _require_admin(settings: Settings, authorization: str) -> None:
    token = settings.admin_api_token
    if not token:
        raise HTTPException(status_code=503, detail="Admin API is not configured.")
    expected = f"Bearer {token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized.")


def _serialize_tenant(
    ctx,
    *,
    telegram_bot_username: str = "fivecent_test_bot",
    telegram_invite_link: Optional[str] = None,
) -> dict:
    return {
        "tenant_id": ctx.id,
        "agency_name": ctx.agency_name,
        "home_port": ctx.home_port,
        "currency": ctx.currency,
        "whatsapp_phone_number_id": ctx.whatsapp_phone_number_id,
        "whatsapp_waba_id": ctx.whatsapp_waba_id,
        "vat_rate": str(ctx.vat_rate),
        "apa_rate": str(ctx.apa_rate),
        "max_discount_pct": str(ctx.max_discount_pct),
        "service_type": ctx.service_type,
        "llm_provider": ctx.llm_provider,
        "llm_model": ctx.llm_model,
        "routing_mode": ctx.routing_mode,
        "feature_flags": ctx.feature_flags.as_dict(),
        "telegram_chat_id": ctx.telegram_chat_id,
        "telegram_invite_link": telegram_invite_link,
        "is_active": ctx.is_active,
        "brokers": [
            {
                "broker_id": b.id,
                "name": b.name,
                "phone_number": b.phone_number,
                "telegram_chat_id": b.telegram_chat_id,
                "role": b.role,
                "is_active": b.is_active,
            }
            for b in ctx.brokers
        ],
    }


def register_internal_routes(app, *, get_container, get_settings_fn) -> None:
    router = APIRouter(prefix="/internal", tags=["internal-admin"])

    def admin(
        authorization: str = Header(default=""),
    ) -> None:
        _require_admin(get_settings_fn(), authorization)

    def tenants() -> SqlTenantRepo:
        container: Container = get_container()
        repo = container.tenants
        if not isinstance(repo, SqlTenantRepo):
            raise HTTPException(status_code=500, detail="Tenant admin is unavailable.")
        return repo

    def conversations() -> SqlConversationRepo:
        container: Container = get_container()
        repo = container.conversations
        if not isinstance(repo, SqlConversationRepo):
            raise HTTPException(status_code=500, detail="Conversation admin is unavailable.")
        return repo

    def bot_username() -> str:
        return get_settings_fn().telegram_bot_username or "fivecent_test_bot"

    async def serialize(ctx) -> dict:
        invite = await tenants().latest_open_telegram_invite(
            ctx.id, bot_username=bot_username()
        )
        return _serialize_tenant(
            ctx,
            telegram_bot_username=bot_username(),
            telegram_invite_link=(invite or {}).get("invite_link"),
        )

    def validate_plugins(service_type: str, llm_provider: str) -> None:
        container: Container = get_container()
        if service_type not in container.services.keys:
            raise HTTPException(status_code=422, detail="Service type is not configured.")
        if llm_provider not in container.providers.keys:
            raise HTTPException(status_code=422, detail="LLM provider is not configured.")

    def config_http(exc: TenantConfigError) -> HTTPException:
        return HTTPException(status_code=422, detail=str(exc))

    @router.get("/tenants", dependencies=[Depends(admin)])
    async def list_tenants() -> list[dict]:
        return [await serialize(ctx) for ctx in await tenants().list_all()]

    @router.post("/tenants", dependencies=[Depends(admin)])
    async def create_tenant(payload: TenantCreate) -> dict:
        try:
            validate_plugins(payload.service_type, payload.llm_provider)
            repo = tenants()
            if await repo.get(payload.tenant_id):
                raise HTTPException(status_code=409, detail="Tenant already exists.")
            ctx = await repo.create_tenant(
                tenant_id=payload.tenant_id,
                agency_name=payload.agency_name,
                home_port=payload.home_port,
                whatsapp_phone_number_id=payload.whatsapp_phone_number_id,
                whatsapp_waba_id=payload.whatsapp_waba_id,
                currency=payload.currency,
                vat_rate=payload.vat_rate,
                apa_rate=payload.apa_rate,
                max_discount_pct=payload.max_discount_pct,
                service_type=payload.service_type,
                llm_provider=payload.llm_provider,
                llm_model=payload.llm_model,
                routing_mode=payload.routing_mode,
                feature_flags=payload.feature_flags,
            )
            return await serialize(ctx)
        except TenantConfigError as exc:
            raise config_http(exc) from exc

    @router.get("/tenants/{tenant_id}", dependencies=[Depends(admin)])
    async def get_tenant(tenant_id: str) -> dict:
        ctx = await tenants().get(tenant_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        return await serialize(ctx)

    @router.patch("/tenants/{tenant_id}", dependencies=[Depends(admin)])
    async def patch_tenant(tenant_id: str, payload: TenantPatch) -> dict:
        try:
            current = await tenants().get(tenant_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Tenant not found.")
            validate_plugins(
                payload.service_type or current.service_type,
                payload.llm_provider or current.llm_provider,
            )
            ctx = await tenants().update_tenant(
                tenant_id, **payload.model_dump(exclude_unset=True)
            )
            if ctx is None:
                raise HTTPException(status_code=404, detail="Tenant not found.")
            return await serialize(ctx)
        except TenantConfigError as exc:
            raise config_http(exc) from exc

    @router.post("/tenants/{tenant_id}/telegram-invite", dependencies=[Depends(admin)])
    async def create_telegram_invite(tenant_id: str) -> dict:
        invite = await tenants().create_telegram_desk_invite(
            tenant_id, bot_username=bot_username()
        )
        if invite is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        return invite

    @router.post("/tenants/{tenant_id}/brokers", dependencies=[Depends(admin)])
    async def add_broker(tenant_id: str, payload: BrokerCreate) -> dict:
        if await tenants().get(tenant_id) is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        broker = await tenants().add_broker(
            tenant_id=tenant_id,
            name=payload.name,
            phone_number=payload.phone_number,
            telegram_chat_id=payload.telegram_chat_id,
            role=payload.role,
        )
        return {
            "broker_id": broker.id,
            "name": broker.name,
            "phone_number": broker.phone_number,
            "telegram_chat_id": broker.telegram_chat_id,
            "role": broker.role,
            "is_active": broker.is_active,
        }

    @router.patch("/brokers/{broker_id}", dependencies=[Depends(admin)])
    async def patch_broker(broker_id: str, payload: BrokerPatch) -> dict:
        data = payload.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(status_code=422, detail="No broker fields to update.")
        clear_telegram = "telegram_chat_id" in data and data.get("telegram_chat_id") in {
            None,
            "",
        }
        broker = await tenants().update_broker(
            broker_id,
            is_active=data.get("is_active"),
            telegram_chat_id=None if clear_telegram else data.get("telegram_chat_id"),
            clear_telegram=clear_telegram,
        )
        if broker is None:
            raise HTTPException(status_code=404, detail="Broker not found.")
        return {
            "broker_id": broker.id,
            "name": broker.name,
            "phone_number": broker.phone_number,
            "telegram_chat_id": broker.telegram_chat_id,
            "role": broker.role,
            "is_active": broker.is_active,
        }

    @router.post("/whatsapp/test-send", dependencies=[Depends(admin)])
    async def whatsapp_test_send(payload: WhatsAppTestSend) -> dict:
        from fivecent.messaging.plugin_whatsapp.outbound import normalize_whatsapp_recipient

        container: Container = get_container()
        tenant_id = (payload.tenant_id or container.seed_tenant_id).strip()
        tenant = await tenants().get(tenant_id)
        if tenant is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        to = normalize_whatsapp_recipient(payload.phone)
        if len(to) < 8:
            raise HTTPException(status_code=422, detail="Phone number is too short.")
        try:
            outbound_id = await container.sender.send(
                tenant_id, "whatsapp", to, payload.message.strip()
            )
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"WhatsApp send failed: {exc}"
            ) from exc
        return {
            "status": "ok",
            "tenant_id": tenant_id,
            "to": to,
            "outbound_id": outbound_id,
        }

    @router.get("/inbound/failed", dependencies=[Depends(admin)])
    async def list_failed_inbound() -> list[dict]:
        return await conversations().list_failed_raw_inbound()

    @router.post("/inbound/{payload_id}/replay", dependencies=[Depends(admin)])
    async def replay_inbound(payload_id: str, request: Request) -> dict:
        repo = conversations()
        row = await repo.get_raw_inbound(payload_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Inbound payload not found.")
        try:
            body = json.loads(row["body"] or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Stored payload is not valid JSON.") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Stored payload is not a JSON object.")
        process = getattr(request.app.state, "process_webhook_payload", None)
        if process is None:
            raise HTTPException(status_code=503, detail="Webhook replay is unavailable.")
        raw = (row["body"] or "").encode("utf-8")
        try:
            await process(row["channel"], body, raw, payload_id=payload_id)
        except Exception as exc:
            await repo.mark_raw_inbound(payload_id, status="failed", error=str(exc)[:2000])
            raise HTTPException(status_code=500, detail="Replay failed.") from exc
        await repo.mark_raw_inbound(payload_id, status="replayed", error=None)
        return {"status": "ok", "payload_id": payload_id}

    @router.get("/alerts/pending", dependencies=[Depends(admin)])
    async def list_pending_alerts() -> list[dict]:
        yachting = container.services.get("yachting")
        outbox = getattr(yachting, "_alert_outbox", None)
        if outbox is None:
            return []
        return list(await outbox.list_pending())

    @router.post("/alerts/flush", dependencies=[Depends(admin)])
    async def flush_pending_alerts() -> dict:
        yachting = container.services.get("yachting")
        flush = getattr(yachting, "flush_broker_alerts", None)
        if flush is None:
            raise HTTPException(status_code=503, detail="Alert outbox is unavailable.")
        result = await flush()
        return {"status": "ok", **result}

    app.include_router(router)
