from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from fivecent.adapters.persistence.tenants import SqlTenantRepo
from fivecent.application.ports import Container
from fivecent.config import Settings

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
    is_active: Optional[bool] = None


class BrokerCreate(BaseModel):
    name: str
    phone_number: str
    telegram_chat_id: Optional[str] = None
    role: str = "broker"


class BrokerPatch(BaseModel):
    is_active: bool


def _require_admin(settings: Settings, authorization: str) -> None:
    token = settings.admin_api_token
    if not token:
        raise HTTPException(status_code=503, detail="Admin API is not configured.")
    expected = f"Bearer {token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized.")


def _serialize_tenant(ctx) -> dict:
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

    def validate_plugins(service_type: str, llm_provider: str) -> None:
        container: Container = get_container()
        if service_type not in container.services.keys:
            raise HTTPException(status_code=422, detail="Service type is not configured.")
        if llm_provider not in container.providers.keys:
            raise HTTPException(status_code=422, detail="LLM provider is not configured.")

    @router.get("/tenants", dependencies=[Depends(admin)])
    async def list_tenants() -> list[dict]:
        return [_serialize_tenant(ctx) for ctx in await tenants().list_all()]

    @router.post("/tenants", dependencies=[Depends(admin)])
    async def create_tenant(payload: TenantCreate) -> dict:
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
        return _serialize_tenant(ctx)

    @router.get("/tenants/{tenant_id}", dependencies=[Depends(admin)])
    async def get_tenant(tenant_id: str) -> dict:
        ctx = await tenants().get(tenant_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        return _serialize_tenant(ctx)

    @router.patch("/tenants/{tenant_id}", dependencies=[Depends(admin)])
    async def patch_tenant(tenant_id: str, payload: TenantPatch) -> dict:
        current = await tenants().get(tenant_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        validate_plugins(
            payload.service_type or current.service_type,
            payload.llm_provider or current.llm_provider,
        )
        ctx = await tenants().update_tenant(tenant_id, **payload.model_dump(exclude_unset=True))
        if ctx is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        return _serialize_tenant(ctx)

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
            "role": broker.role,
            "is_active": broker.is_active,
        }

    @router.patch("/brokers/{broker_id}", dependencies=[Depends(admin)])
    async def patch_broker(broker_id: str, payload: BrokerPatch) -> dict:
        ok = await tenants().set_broker_active(broker_id, payload.is_active)
        if not ok:
            raise HTTPException(status_code=404, detail="Broker not found.")
        return {"broker_id": broker_id, "is_active": payload.is_active}

    app.include_router(router)
