from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from fivecent.adapters.persistence.ops import SqlOpsRepo, commercial_context
from fivecent.adapters.persistence.repos import SqlConversationRepo
from fivecent.adapters.persistence.tenants import SqlTenantRepo
from fivecent.adapters.persistence.users import SqlUserRepo
from fivecent.application.ports import Container
from fivecent.auth.roles import PLATFORM_ACTOR, Actor, Role
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


class BoatUpsert(BaseModel):
    boat_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    length_m: Decimal
    capacity: int = Field(ge=1, le=12)
    low_season_daily_rate: Decimal
    high_season_daily_rate: Decimal
    skipper_daily_rate: Decimal
    active: bool = True


class FleetImport(BaseModel):
    boats: list[dict] = Field(min_length=1, max_length=200)


class BoatActivePatch(BaseModel):
    active: bool


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    role: Role
    tenant_id: Optional[str] = None


class ClientInvite(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    tenant_id: str = Field(min_length=1, max_length=64)


class PortalSetPassword(BaseModel):
    invite_token: str = Field(min_length=16, max_length=128)
    password: str = Field(min_length=12, max_length=128)


class PortalLogin(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=128)


class ConversationOpsPatch(BaseModel):
    assigned_broker_id: Optional[str] = None
    handoff_status: Optional[str] = Field(
        default=None, pattern="^(open|assigned|taken_over|resolved)$"
    )
    clear_assignment: bool = False


class ManualReply(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


async def _resolve_actor(
    settings: Settings,
    authorization: str,
    *,
    users: Optional[SqlUserRepo] = None,
) -> Actor:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized.")
    env_token = settings.admin_api_token
    if env_token and token == env_token:
        return PLATFORM_ACTOR
    if users is not None:
        actor = await users.resolve_bearer(token)
        if actor is not None:
            return actor
    if not env_token:
        raise HTTPException(status_code=503, detail="Admin API is not configured.")
    raise HTTPException(status_code=401, detail="Unauthorized.")


def _require_role(actor: Actor, minimum: Role) -> None:
    if actor.is_client():
        raise HTTPException(
            status_code=403, detail="Client portal users cannot access company admin."
        )
    if not actor.can(minimum):
        raise HTTPException(status_code=403, detail="Insufficient role.")


def _require_tenant(actor: Actor, tenant_id: str) -> None:
    if not actor.may_access_tenant(tenant_id):
        raise HTTPException(status_code=403, detail="Tenant access denied.")


def _require_client(actor: Actor) -> None:
    if not actor.is_client() or not actor.tenant_id:
        raise HTTPException(status_code=403, detail="Client portal access required.")


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

    def tenants() -> SqlTenantRepo:
        container: Container = get_container()
        repo = container.tenants
        if not isinstance(repo, SqlTenantRepo):
            raise HTTPException(status_code=500, detail="Tenant admin is unavailable.")
        return repo

    def users() -> SqlUserRepo:
        sessions = getattr(tenants(), "_sessions", None)
        if sessions is None:
            raise HTTPException(status_code=500, detail="User admin is unavailable.")
        return SqlUserRepo(sessions)

    def ops() -> SqlOpsRepo:
        sessions = getattr(tenants(), "_sessions", None)
        if sessions is None:
            raise HTTPException(status_code=500, detail="Ops admin is unavailable.")
        return SqlOpsRepo(sessions)

    async def actor(
        authorization: str = Header(default=""),
    ) -> Actor:
        return await _resolve_actor(
            get_settings_fn(), authorization, users=users()
        )

    def fleet_admin():
        from fivecent.services.plugin_yachting.persistence.fleet_admin import (
            SqlFleetAdminRepo,
        )

        sessions = getattr(tenants(), "_sessions", None)
        if sessions is None:
            raise HTTPException(status_code=500, detail="Fleet admin is unavailable.")
        return SqlFleetAdminRepo(sessions)

    def conversations() -> SqlConversationRepo:
        container: Container = get_container()
        repo = container.conversations
        if not isinstance(repo, SqlConversationRepo):
            raise HTTPException(status_code=500, detail="Conversation admin is unavailable.")
        return repo

    def bot_username() -> str:
        return get_settings_fn().telegram_bot_username or "fivecent_test_bot"

    async def audit_write(
        current: Actor,
        *,
        tenant_id: Optional[str],
        action: str,
        resource_type: str,
        resource_id: Optional[str],
        before: Optional[dict] = None,
        after: Optional[dict] = None,
    ) -> None:
        await users().record_audit(
            actor=current,
            tenant_id=tenant_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=before,
            after=after,
        )

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

    @router.get("/tenants")
    async def list_tenants(
        current: Actor = Depends(actor),
        q: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        _require_role(current, "broker")
        scoped = None if current.role == "platform_admin" else current.tenant_id
        if current.role != "platform_admin" and not scoped:
            raise HTTPException(status_code=403, detail="Tenant scope required.")
        page = await tenants().list_page(
            q=q, offset=offset, limit=limit, tenant_id=scoped
        )
        return {
            "total": page["total"],
            "offset": page["offset"],
            "limit": page["limit"],
            "items": [await serialize(ctx) for ctx in page["items"]],
        }

    @router.post("/tenants")
    async def create_tenant(
        payload: TenantCreate, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "platform_admin")
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
            body = await serialize(ctx)
            await audit_write(
                current,
                tenant_id=payload.tenant_id,
                action="create",
                resource_type="tenant",
                resource_id=payload.tenant_id,
                after={"agency_name": payload.agency_name},
            )
            return body
        except TenantConfigError as exc:
            raise config_http(exc) from exc

    @router.get("/tenants/{tenant_id}")
    async def get_tenant(tenant_id: str, current: Actor = Depends(actor)) -> dict:
        _require_role(current, "broker")
        _require_tenant(current, tenant_id)
        ctx = await tenants().get(tenant_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        return await serialize(ctx)

    @router.patch("/tenants/{tenant_id}")
    async def patch_tenant(
        tenant_id: str, payload: TenantPatch, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "agency_admin")
        _require_tenant(current, tenant_id)
        try:
            existing = await tenants().get(tenant_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="Tenant not found.")
            validate_plugins(
                payload.service_type or existing.service_type,
                payload.llm_provider or existing.llm_provider,
            )
            before = {"agency_name": existing.agency_name, "is_active": existing.is_active}
            ctx = await tenants().update_tenant(
                tenant_id, **payload.model_dump(exclude_unset=True)
            )
            if ctx is None:
                raise HTTPException(status_code=404, detail="Tenant not found.")
            body = await serialize(ctx)
            await audit_write(
                current,
                tenant_id=tenant_id,
                action="update",
                resource_type="tenant",
                resource_id=tenant_id,
                before=before,
                after={"agency_name": ctx.agency_name, "is_active": ctx.is_active},
            )
            return body
        except TenantConfigError as exc:
            raise config_http(exc) from exc

    @router.post("/tenants/{tenant_id}/telegram-invite")
    async def create_telegram_invite(
        tenant_id: str, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "agency_admin")
        _require_tenant(current, tenant_id)
        invite = await tenants().create_telegram_desk_invite(
            tenant_id, bot_username=bot_username()
        )
        if invite is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        await audit_write(
            current,
            tenant_id=tenant_id,
            action="create",
            resource_type="telegram_invite",
            resource_id=tenant_id,
        )
        return invite

    @router.get("/tenants/{tenant_id}/setup-status")
    async def tenant_setup_status(
        tenant_id: str, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "broker")
        _require_tenant(current, tenant_id)
        from fivecent.config import get_settings as _gs

        ctx = await tenants().get(tenant_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        boats = await fleet_admin().list_boats(tenant_id)
        active_boats = sum(1 for boat in boats if boat.active)
        settings = _gs()
        phone_ok = bool(
            (ctx.whatsapp_phone_number_id or "").strip()
            and ctx.whatsapp_phone_number_id != "seed-phone-number-id"
        )
        desk_ok = bool((ctx.telegram_chat_id or "").strip())
        broker_ok = any(
            (b.telegram_chat_id or "").strip() for b in ctx.active_brokers
        ) or desk_ok
        checks = {
            "agency_active": bool(ctx.is_active),
            "whatsapp_phone_configured": phone_ok,
            "has_active_boat": active_boats > 0,
            "active_boat_count": active_boats,
            "telegram_desk_or_broker": broker_ok,
            "telegram_bot_configured": bool(settings.telegram_bot_token),
            # Never return secret values — presence only.
            "whatsapp_access_token_present": bool(settings.whatsapp_access_token),
        }
        if not ctx.is_active or active_boats == 0:
            status = "draft"
        elif not phone_ok or not checks["whatsapp_access_token_present"]:
            status = "misconfigured"
        elif not broker_ok:
            status = "misconfigured"
        else:
            status = "ready"
        return {
            "tenant_id": tenant_id,
            "status": status,
            "checks": checks,
        }

    @router.get("/tenants/{tenant_id}/fleet")
    async def list_fleet(
        tenant_id: str, current: Actor = Depends(actor)
    ) -> list[dict]:
        _require_role(current, "broker")
        _require_tenant(current, tenant_id)
        if await tenants().get(tenant_id) is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        return [boat.as_dict() for boat in await fleet_admin().list_boats(tenant_id)]

    @router.post("/tenants/{tenant_id}/fleet")
    async def upsert_fleet_boat(
        tenant_id: str, payload: BoatUpsert, current: Actor = Depends(actor)
    ) -> dict:
        from fivecent.services.plugin_yachting.persistence.fleet_admin import (
            FleetValidationError,
        )

        _require_role(current, "agency_admin")
        _require_tenant(current, tenant_id)
        if await tenants().get(tenant_id) is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        try:
            boat = await fleet_admin().upsert_boat(
                tenant_id,
                boat_id=payload.boat_id,
                name=payload.name,
                length_m=payload.length_m,
                capacity=payload.capacity,
                low_season_daily_rate=payload.low_season_daily_rate,
                high_season_daily_rate=payload.high_season_daily_rate,
                skipper_daily_rate=payload.skipper_daily_rate,
                active=payload.active,
            )
        except FleetValidationError as exc:
            raise HTTPException(
                status_code=422, detail={"message": str(exc), "field": exc.field}
            ) from exc
        await audit_write(
            current,
            tenant_id=tenant_id,
            action="upsert",
            resource_type="fleet_boat",
            resource_id=boat.boat_id,
            after={"name": boat.name, "active": boat.active},
        )
        return boat.as_dict()

    @router.patch("/tenants/{tenant_id}/fleet/{boat_id}")
    async def patch_fleet_boat(
        tenant_id: str,
        boat_id: str,
        payload: BoatActivePatch,
        current: Actor = Depends(actor),
    ) -> dict:
        _require_role(current, "agency_admin")
        _require_tenant(current, tenant_id)
        boat = await fleet_admin().set_active(tenant_id, boat_id, payload.active)
        if boat is None:
            raise HTTPException(status_code=404, detail="Boat not found.")
        await audit_write(
            current,
            tenant_id=tenant_id,
            action="update",
            resource_type="fleet_boat",
            resource_id=boat_id,
            after={"active": payload.active},
        )
        return boat.as_dict()

    @router.post("/tenants/{tenant_id}/fleet/import")
    async def import_fleet(
        tenant_id: str, payload: FleetImport, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "agency_admin")
        _require_tenant(current, tenant_id)
        if await tenants().get(tenant_id) is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        result = await fleet_admin().import_boats(tenant_id, payload.boats)
        status = 200 if not result["errors"] else 207
        return {"status": status, **result}

    @router.post("/tenants/{tenant_id}/fleet/seed-starter")
    async def seed_starter_fleet(
        tenant_id: str, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "agency_admin")
        _require_tenant(current, tenant_id)
        if await tenants().get(tenant_id) is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        boats = await fleet_admin().seed_starter_fleet(tenant_id)
        return {"boats": [boat.as_dict() for boat in boats]}

    @router.post("/tenants/{tenant_id}/brokers")
    async def add_broker(
        tenant_id: str, payload: BrokerCreate, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "agency_admin")
        _require_tenant(current, tenant_id)
        if await tenants().get(tenant_id) is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        broker = await tenants().add_broker(
            tenant_id=tenant_id,
            name=payload.name,
            phone_number=payload.phone_number,
            telegram_chat_id=payload.telegram_chat_id,
            role=payload.role,
        )
        await audit_write(
            current,
            tenant_id=tenant_id,
            action="create",
            resource_type="broker",
            resource_id=broker.id,
            after={"name": broker.name, "role": broker.role},
        )
        return {
            "broker_id": broker.id,
            "name": broker.name,
            "phone_number": broker.phone_number,
            "telegram_chat_id": broker.telegram_chat_id,
            "role": broker.role,
            "is_active": broker.is_active,
        }

    @router.patch("/brokers/{broker_id}")
    async def patch_broker(
        broker_id: str, payload: BrokerPatch, current: Actor = Depends(actor)
    ) -> dict:
        from fivecent.adapters.persistence.models import Broker as BrokerRow

        _require_role(current, "agency_admin")
        data = payload.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(status_code=422, detail="No broker fields to update.")
        sessions = getattr(tenants(), "_sessions")
        async with sessions() as session:
            row = await session.get(BrokerRow, broker_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Broker not found.")
            broker_tenant_id = row.tenant_id
        _require_tenant(current, broker_tenant_id)
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

    @router.post("/whatsapp/test-send")
    async def whatsapp_test_send(
        payload: WhatsAppTestSend, current: Actor = Depends(actor)
    ) -> dict:
        from fivecent.messaging.plugin_whatsapp.outbound import normalize_whatsapp_recipient

        _require_role(current, "agency_admin")
        container: Container = get_container()
        tenant_id = (payload.tenant_id or container.seed_tenant_id).strip()
        _require_tenant(current, tenant_id)
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

    @router.get("/inbound/failed")
    async def list_failed_inbound(current: Actor = Depends(actor)) -> list[dict]:
        _require_role(current, "agency_admin")
        rows = await conversations().list_failed_raw_inbound()
        if current.role == "platform_admin":
            return rows
        return [row for row in rows if row.get("tenant_id") == current.tenant_id]

    @router.post("/inbound/{payload_id}/replay")
    async def replay_inbound(
        payload_id: str, request: Request, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "agency_admin")
        repo = conversations()
        row = await repo.get_raw_inbound(payload_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Inbound payload not found.")
        if row.get("tenant_id"):
            _require_tenant(current, row["tenant_id"])
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

    @router.get("/alerts/pending")
    async def list_pending_alerts(current: Actor = Depends(actor)) -> list[dict]:
        _require_role(current, "agency_admin")
        yachting = get_container().services.get("yachting")
        outbox = getattr(yachting, "_alert_outbox", None)
        if outbox is None:
            return []
        rows = list(await outbox.list_pending())
        if current.role == "platform_admin":
            return rows
        return [row for row in rows if row.get("tenant_id") == current.tenant_id]

    @router.post("/alerts/flush")
    async def flush_pending_alerts(current: Actor = Depends(actor)) -> dict:
        _require_role(current, "platform_admin")
        yachting = get_container().services.get("yachting")
        flush = getattr(yachting, "flush_broker_alerts", None)
        if flush is None:
            raise HTTPException(status_code=503, detail="Alert outbox is unavailable.")
        result = await flush()
        return {"status": "ok", **result}

    # --- ED-7 inbox ---------------------------------------------------------

    @router.get("/tenants/{tenant_id}/conversations")
    async def list_inbox(
        tenant_id: str,
        current: Actor = Depends(actor),
        q: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        _require_role(current, "broker")
        _require_tenant(current, tenant_id)
        if await tenants().get(tenant_id) is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        return await conversations().list_conversations(
            tenant_id, q=q, offset=offset, limit=limit
        )

    @router.get("/tenants/{tenant_id}/conversations/{conversation_id}")
    async def get_inbox_conversation(
        tenant_id: str, conversation_id: str, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "broker")
        _require_tenant(current, tenant_id)
        detail = await conversations().get_conversation(tenant_id, conversation_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        sessions = getattr(tenants(), "_sessions")
        detail["commercial"] = await commercial_context(
            sessions, tenant_id=tenant_id, conversation_id=conversation_id
        )
        human = await get_container().cache.is_human_active(
            tenant_id, detail["channel"], detail["user_channel_id"]
        )
        detail["human_active"] = human
        return detail

    @router.patch("/tenants/{tenant_id}/conversations/{conversation_id}")
    async def patch_inbox_conversation(
        tenant_id: str,
        conversation_id: str,
        payload: ConversationOpsPatch,
        current: Actor = Depends(actor),
    ) -> dict:
        _require_role(current, "broker")
        _require_tenant(current, tenant_id)
        updated = await conversations().update_conversation_ops(
            tenant_id,
            conversation_id,
            assigned_broker_id=payload.assigned_broker_id,
            handoff_status=payload.handoff_status,
            clear_assignment=payload.clear_assignment,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        await audit_write(
            current,
            tenant_id=tenant_id,
            action="update",
            resource_type="conversation",
            resource_id=conversation_id,
            after=payload.model_dump(exclude_unset=True),
        )
        return updated

    @router.post("/tenants/{tenant_id}/conversations/{conversation_id}/takeover")
    async def takeover_conversation(
        tenant_id: str, conversation_id: str, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "broker")
        _require_tenant(current, tenant_id)
        detail = await conversations().get_conversation(tenant_id, conversation_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        await get_container().cache.set_human_active(
            tenant_id, detail["channel"], detail["user_channel_id"]
        )
        updated = await conversations().update_conversation_ops(
            tenant_id, conversation_id, handoff_status="taken_over"
        )
        await audit_write(
            current,
            tenant_id=tenant_id,
            action="takeover",
            resource_type="conversation",
            resource_id=conversation_id,
        )
        return updated or detail

    @router.post("/tenants/{tenant_id}/conversations/{conversation_id}/resume-bot")
    async def resume_bot(
        tenant_id: str, conversation_id: str, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "broker")
        _require_tenant(current, tenant_id)
        detail = await conversations().get_conversation(tenant_id, conversation_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        await get_container().cache.clear_human_active(
            tenant_id, detail["channel"], detail["user_channel_id"]
        )
        updated = await conversations().update_conversation_ops(
            tenant_id, conversation_id, handoff_status="open"
        )
        await audit_write(
            current,
            tenant_id=tenant_id,
            action="resume_bot",
            resource_type="conversation",
            resource_id=conversation_id,
        )
        return updated or detail

    @router.post("/tenants/{tenant_id}/conversations/{conversation_id}/reply")
    async def manual_reply(
        tenant_id: str,
        conversation_id: str,
        payload: ManualReply,
        current: Actor = Depends(actor),
    ) -> dict:
        _require_role(current, "broker")
        _require_tenant(current, tenant_id)
        detail = await conversations().get_conversation(tenant_id, conversation_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        from fivecent.messaging.errors import SessionWindowClosedError

        container: Container = get_container()
        try:
            outbound_id = await container.sender.send(
                tenant_id,
                detail["channel"],
                detail["user_channel_id"],
                payload.text.strip(),
            )
        except SessionWindowClosedError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "session_window_closed",
                    "message": (
                        "WhatsApp 24h session is closed. Use a supported template "
                        "or hand off outside the bot — free-form send would fail."
                    ),
                    "hint": exc.detail,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        await container.cache.set_human_active(
            tenant_id, detail["channel"], detail["user_channel_id"]
        )
        await conversations().update_conversation_ops(
            tenant_id, conversation_id, handoff_status="taken_over"
        )
        await conversations().append_assistant(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            text=payload.text.strip(),
            provider_message_id=outbound_id,
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
        )
        await audit_write(
            current,
            tenant_id=tenant_id,
            action="manual_reply",
            resource_type="conversation",
            resource_id=conversation_id,
        )
        return {"status": "ok", "outbound_id": outbound_id}

    # --- ED-6 users / audit -------------------------------------------------

    @router.get("/users")
    async def list_users(
        current: Actor = Depends(actor), tenant_id: Optional[str] = None
    ) -> list[dict]:
        _require_role(current, "agency_admin")
        if current.role != "platform_admin":
            tenant_id = current.tenant_id
        return list(await users().list_users(tenant_id=tenant_id))

    @router.post("/users")
    async def create_user(
        payload: UserCreate, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "platform_admin")
        if payload.role != "platform_admin" and not payload.tenant_id:
            raise HTTPException(
                status_code=422, detail="tenant_id is required for non-platform roles."
            )
        if payload.role == "platform_admin":
            payload = UserCreate(
                email=payload.email,
                display_name=payload.display_name,
                role=payload.role,
                tenant_id=None,
            )
        created = await users().create_user(
            email=payload.email,
            display_name=payload.display_name,
            role=payload.role,
            tenant_id=payload.tenant_id,
        )
        await audit_write(
            current,
            tenant_id=payload.tenant_id,
            action="create",
            resource_type="admin_user",
            resource_id=created["user_id"],
            after={
                "email": created["email"],
                "role": created["role"],
                "tenant_id": created["tenant_id"],
            },
        )
        return created

    @router.post("/users/{user_id}/disable")
    async def disable_user(user_id: str, current: Actor = Depends(actor)) -> dict:
        _require_role(current, "agency_admin")
        # Look up before revoke so agency admins can only disable their tenant's clients.
        existing_rows = await users().list_users()
        target = next((row for row in existing_rows if row["user_id"] == user_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if current.role != "platform_admin":
            if target.get("role") != "client_user":
                raise HTTPException(
                    status_code=403, detail="Only platform admins can disable staff accounts."
                )
            if not target.get("tenant_id") or not current.may_access_tenant(target["tenant_id"]):
                raise HTTPException(status_code=403, detail="Tenant access denied.")
        disabled = await users().disable_user(user_id)
        if disabled is None:
            raise HTTPException(status_code=404, detail="User not found.")
        await audit_write(
            current,
            tenant_id=disabled.get("tenant_id"),
            action="disable",
            resource_type="admin_user",
            resource_id=user_id,
            after={"is_active": False},
        )
        return disabled

    @router.get("/audit")
    async def list_audit(
        current: Actor = Depends(actor),
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        _require_role(current, "agency_admin")
        if current.role != "platform_admin":
            tenant_id = current.tenant_id
        return await users().list_audit(tenant_id=tenant_id, limit=min(limit, 500))

    # --- ED-13 metrics / retention ------------------------------------------

    @router.get("/metrics")
    async def metrics(current: Actor = Depends(actor)) -> dict:
        _require_role(current, "agency_admin")
        snap = await ops().metrics_snapshot()
        settings = get_settings_fn()
        snap["load_target"] = {
            "concurrent_conversations": 50,
            "ack_slo_seconds": 2,
            "queue_age_slo_seconds": 30,
            "worker_max_jobs": settings.worker_max_jobs,
            "tenant_rate_limit_per_minute": settings.tenant_rate_limit_per_minute,
        }
        return snap

    @router.get("/tenants/{tenant_id}/usage")
    async def tenant_usage(
        tenant_id: str, current: Actor = Depends(actor), days: int = 30
    ) -> dict:
        _require_role(current, "agency_admin")
        _require_tenant(current, tenant_id)
        rows = await ops().usage_for_tenant(tenant_id, days=days)
        return {"tenant_id": tenant_id, "days": days, "items": rows}

    @router.post("/tenants/{tenant_id}/conversations/{conversation_id}/export")
    async def export_conversation(
        tenant_id: str, conversation_id: str, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "agency_admin")
        _require_tenant(current, tenant_id)
        exported = await ops().export_conversation(tenant_id, conversation_id)
        if exported is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        await audit_write(
            current,
            tenant_id=tenant_id,
            action="export",
            resource_type="conversation",
            resource_id=conversation_id,
        )
        return exported

    @router.delete("/tenants/{tenant_id}/conversations/{conversation_id}")
    async def delete_conversation(
        tenant_id: str, conversation_id: str, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "agency_admin")
        _require_tenant(current, tenant_id)
        ok = await conversations().delete_conversation(tenant_id, conversation_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        await audit_write(
            current,
            tenant_id=tenant_id,
            action="delete",
            resource_type="conversation",
            resource_id=conversation_id,
        )
        return {"status": "ok", "conversation_id": conversation_id}

    @router.post("/ops/retention/run")
    async def run_retention(current: Actor = Depends(actor)) -> dict:
        _require_role(current, "platform_admin")
        settings = get_settings_fn()
        result = await ops().apply_retention(
            message_days=settings.retention_messages_days,
            payload_days=settings.retention_inbound_payloads_days,
            audit_days=settings.retention_audit_days,
        )
        await audit_write(
            current,
            tenant_id=None,
            action="retention",
            resource_type="ops",
            resource_id="retention",
            after=result,
        )
        return result

    @router.get("/me")
    async def whoami(current: Actor = Depends(actor)) -> dict:
        return {
            "id": current.id,
            "email": current.email,
            "role": current.role,
            "tenant_id": current.tenant_id,
            "workspace": "client_portal" if current.is_client() else "company_admin",
        }

    # --- ED-15 client portal (read-only analytics) ---------------------------

    @router.post("/portal/invite")
    async def invite_client_user(
        payload: ClientInvite, current: Actor = Depends(actor)
    ) -> dict:
        _require_role(current, "agency_admin")
        _require_tenant(current, payload.tenant_id)
        if await tenants().get(payload.tenant_id) is None:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        try:
            invited = await users().invite_client(
                email=payload.email,
                display_name=payload.display_name,
                tenant_id=payload.tenant_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await audit_write(
            current,
            tenant_id=payload.tenant_id,
            action="invite",
            resource_type="client_user",
            resource_id=invited["user_id"],
            after={"email": invited["email"], "role": "client_user"},
        )
        return invited

    @router.post("/portal/set-password")
    async def portal_set_password(payload: PortalSetPassword) -> dict:
        try:
            result = await users().set_password_from_invite(
                payload.invite_token, payload.password
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await users().record_audit(
            actor=None,
            tenant_id=result.get("tenant_id"),
            action="set_password",
            resource_type="client_user",
            resource_id=result["user_id"],
        )
        return {
            "user_id": result["user_id"],
            "email": result["email"],
            "display_name": result["display_name"],
            "role": result["role"],
            "tenant_id": result["tenant_id"],
            "api_token": result["api_token"],
        }

    @router.post("/portal/login")
    async def portal_login(payload: PortalLogin) -> dict:
        try:
            result = await users().authenticate_password(payload.email, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {
            "user_id": result["user_id"],
            "email": result["email"],
            "display_name": result["display_name"],
            "role": result["role"],
            "tenant_id": result["tenant_id"],
            "api_token": result["api_token"],
        }

    @router.get("/portal/overview")
    async def portal_overview(current: Actor = Depends(actor)) -> dict:
        _require_client(current)
        assert current.tenant_id is not None
        ctx = await tenants().get(current.tenant_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="Agency not found.")
        return {
            "tenant_id": ctx.id,
            "agency_name": ctx.agency_name,
            "home_port": ctx.home_port,
            "currency": ctx.currency,
            "is_active": ctx.is_active,
            "user": {
                "email": current.email,
                "role": current.role,
            },
        }

    @router.get("/portal/analytics")
    async def portal_analytics(
        current: Actor = Depends(actor),
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> dict:
        _require_client(current)
        assert current.tenant_id is not None
        try:
            return await ops().agency_analytics(
                current.tenant_id, date_from=date_from, date_to=date_to
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    app.include_router(router)
