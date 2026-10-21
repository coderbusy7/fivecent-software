from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.adapters.persistence.models import Broker as BrokerRow
from fivecent.adapters.persistence.models import Tenant
from fivecent.domain.tenancy import (
    DEFAULT_FEATURE_FLAGS,
    Broker,
    FeatureFlags,
    TenantContext,
)


def _context(row: Tenant, brokers: list[BrokerRow]) -> TenantContext:
    flags = row.feature_flags if isinstance(row.feature_flags, dict) else {}
    routing = row.routing_mode if row.routing_mode in {"broadcast", "round_robin"} else "broadcast"
    return TenantContext(
        id=row.id,
        agency_name=row.agency_name,
        home_port=row.home_port,
        currency=row.currency or "EUR",
        whatsapp_phone_number_id=row.whatsapp_phone_number_id,
        whatsapp_waba_id=row.whatsapp_waba_id or "",
        vat_rate=Decimal(row.default_vat_rate),
        apa_rate=Decimal(row.default_apa_rate),
        max_discount_pct=Decimal(row.max_discount_pct),
        routing_mode=routing,  # type: ignore[arg-type]
        feature_flags=FeatureFlags.from_mapping(flags),
        service_type=row.service_type or "yachting",
        llm_provider=row.llm_provider or "openai",
        llm_model=row.llm_model or "gpt-4o-mini",
        brokers=tuple(
            Broker(
                id=b.id,
                name=b.name,
                phone_number=b.phone_number,
                telegram_chat_id=b.telegram_chat_id,
                role=b.role,
                is_active=b.is_active,
            )
            for b in brokers
        ),
        is_active=row.is_active,
    )


class SqlTenantRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._routing_cache: dict[str, tuple[float, dict[str, str]]] = {}

    async def list_all(self) -> list[TenantContext]:
        async with self._sessions() as session:
            tenants = (await session.execute(select(Tenant).order_by(Tenant.agency_name))).scalars().all()
            brokers = (await session.execute(select(BrokerRow))).scalars().all()
        by_tenant: dict[str, list[BrokerRow]] = {}
        for broker in brokers:
            by_tenant.setdefault(broker.tenant_id, []).append(broker)
        return [_context(row, by_tenant.get(row.id, [])) for row in tenants]

    async def get(self, tenant_id: str) -> Optional[TenantContext]:
        async with self._sessions() as session:
            row = await session.get(Tenant, tenant_id)
            if row is None:
                return None
            brokers = await session.execute(select(BrokerRow).where(BrokerRow.tenant_id == tenant_id))
            return _context(row, list(brokers.scalars().all()))

    async def id_for_whatsapp_phone(self, phone_number_id: str) -> Optional[str]:
        mapping = await self.whatsapp_routing_map()
        return mapping.get(phone_number_id)

    async def whatsapp_routing_map(self) -> dict[str, str]:
        return await self.channel_routing_map("whatsapp")

    async def channel_routing_map(self, channel: str) -> dict[str, str]:
        from time import time

        now = time()
        cached = self._routing_cache.get(channel)
        if cached and cached[0] > now:
            return cached[1]
        columns = {
            "whatsapp": Tenant.whatsapp_phone_number_id,
            "instagram": Tenant.instagram_account_id,
            "messenger": Tenant.messenger_page_id,
        }
        column = columns.get(channel)
        if column is None:
            return {}
        async with self._sessions() as session:
            result = await session.execute(select(Tenant.id, column).where(Tenant.is_active.is_(True)))
            mapping = {phone: tenant_id for tenant_id, phone in result.all() if phone}
        self._routing_cache[channel] = (now + 30, mapping)
        return mapping

    def invalidate_routing(self) -> None:
        self._routing_cache.clear()

    async def create_tenant(
        self,
        *,
        tenant_id: str,
        agency_name: str,
        home_port: str,
        whatsapp_phone_number_id: str,
        whatsapp_waba_id: str = "",
        currency: str = "EUR",
        vat_rate: Decimal = Decimal("0.20"),
        apa_rate: Decimal = Decimal("0.30"),
        max_discount_pct: Decimal = Decimal("0.08"),
        service_type: str = "yachting",
        llm_provider: str = "openai",
        llm_model: str = "gpt-4o-mini",
        routing_mode: str = "broadcast",
        feature_flags: Optional[dict] = None,
    ) -> TenantContext:
        flags = {**DEFAULT_FEATURE_FLAGS, **(feature_flags or {})}
        async with self._sessions() as session:
            session.add(
                Tenant(
                    id=tenant_id,
                    agency_name=agency_name,
                    home_port=home_port,
                    currency=currency,
                    whatsapp_phone_number_id=whatsapp_phone_number_id,
                    whatsapp_waba_id=whatsapp_waba_id,
                    default_vat_rate=vat_rate,
                    default_apa_rate=apa_rate,
                    max_discount_pct=max_discount_pct,
                    service_type=service_type,
                    llm_provider=llm_provider,
                    llm_model=llm_model,
                    routing_mode=routing_mode,
                    feature_flags=flags,
                    is_active=True,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        self.invalidate_routing()
        context = await self.get(tenant_id)
        assert context is not None
        return context

    async def update_tenant(self, tenant_id: str, **fields) -> Optional[TenantContext]:
        async with self._sessions() as session:
            row = await session.get(Tenant, tenant_id)
            if row is None:
                return None
            mapping = {
                "agency_name": "agency_name",
                "home_port": "home_port",
                "currency": "currency",
                "whatsapp_phone_number_id": "whatsapp_phone_number_id",
                "whatsapp_waba_id": "whatsapp_waba_id",
                "vat_rate": "default_vat_rate",
                "apa_rate": "default_apa_rate",
                "max_discount_pct": "max_discount_pct",
                "service_type": "service_type",
                "llm_provider": "llm_provider",
                "llm_model": "llm_model",
                "routing_mode": "routing_mode",
                "feature_flags": "feature_flags",
                "is_active": "is_active",
            }
            for key, column in mapping.items():
                if key in fields and fields[key] is not None:
                    value = fields[key]
                    if key == "feature_flags":
                        current = row.feature_flags if isinstance(row.feature_flags, dict) else {}
                        value = {**DEFAULT_FEATURE_FLAGS, **current, **value}
                    setattr(row, column, value)
            await session.commit()
        self.invalidate_routing()
        return await self.get(tenant_id)

    async def add_broker(
        self,
        *,
        tenant_id: str,
        name: str,
        phone_number: str,
        telegram_chat_id: Optional[str] = None,
        role: str = "broker",
    ) -> Broker:
        broker_id = str(uuid4())
        async with self._sessions() as session:
            session.add(
                BrokerRow(
                    id=broker_id,
                    tenant_id=tenant_id,
                    name=name,
                    phone_number=phone_number,
                    telegram_chat_id=telegram_chat_id,
                    role=role,
                    is_active=True,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        return Broker(
            id=broker_id,
            name=name,
            phone_number=phone_number,
            telegram_chat_id=telegram_chat_id,
            role=role,
            is_active=True,
        )

    async def set_broker_active(self, broker_id: str, is_active: bool) -> bool:
        async with self._sessions() as session:
            row = await session.get(BrokerRow, broker_id)
            if row is None:
                return False
            row.is_active = is_active
            await session.commit()
            return True
