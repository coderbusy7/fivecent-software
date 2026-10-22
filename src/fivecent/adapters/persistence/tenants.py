from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.adapters.persistence.models import Broker as BrokerRow
from fivecent.adapters.persistence.models import TelegramDeskInvite, Tenant
from fivecent.domain.tenancy import (
    Broker,
    FeatureFlags,
    TenantContext,
    validate_feature_flags,
    validate_rate,
    validate_routing_mode,
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
        telegram_chat_id=row.telegram_chat_id,
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
        vat_rate = validate_rate("vat_rate", vat_rate)
        apa_rate = validate_rate("apa_rate", apa_rate)
        max_discount_pct = validate_rate("max_discount_pct", max_discount_pct)
        routing_mode = validate_routing_mode(routing_mode)
        flags = validate_feature_flags(feature_flags)
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
                "telegram_chat_id": "telegram_chat_id",
                "is_active": "is_active",
            }
            for key, column in mapping.items():
                if key not in fields:
                    continue
                value = fields[key]
                if key == "telegram_chat_id":
                    # Allow clearing desk chat id with null or blank.
                    row.telegram_chat_id = (
                        None if value is None else (str(value).strip() or None)
                    )
                    continue
                if value is None:
                    continue
                if key in {"vat_rate", "apa_rate", "max_discount_pct"}:
                    value = validate_rate(key, Decimal(str(value)))
                elif key == "routing_mode":
                    value = validate_routing_mode(str(value))
                elif key == "feature_flags":
                    current = row.feature_flags if isinstance(row.feature_flags, dict) else {}
                    value = validate_feature_flags({**current, **value})
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

    async def update_broker(
        self,
        broker_id: str,
        *,
        is_active: Optional[bool] = None,
        telegram_chat_id: Optional[str] = None,
        clear_telegram: bool = False,
    ) -> Optional[Broker]:
        async with self._sessions() as session:
            row = await session.get(BrokerRow, broker_id)
            if row is None:
                return None
            if is_active is not None:
                row.is_active = is_active
            if clear_telegram:
                row.telegram_chat_id = None
            elif telegram_chat_id is not None:
                row.telegram_chat_id = telegram_chat_id.strip() or None
            await session.commit()
            return Broker(
                id=row.id,
                name=row.name,
                phone_number=row.phone_number,
                telegram_chat_id=row.telegram_chat_id,
                role=row.role,
                is_active=row.is_active,
            )

    async def set_tenant_telegram_chat_id(
        self, tenant_id: str, telegram_chat_id: str
    ) -> Optional[TenantContext]:
        return await self.update_tenant(
            tenant_id, telegram_chat_id=str(telegram_chat_id).strip()
        )

    async def create_telegram_desk_invite(
        self,
        tenant_id: str,
        *,
        ttl_hours: int = 24,
        bot_username: str = "fivecent_test_bot",
    ) -> Optional[dict[str, Any]]:
        ctx = await self.get(tenant_id)
        if ctx is None or not ctx.is_active:
            return None
        hours = max(1, min(int(ttl_hours), 168))
        token = secrets.token_urlsafe(18)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=hours)
        async with self._sessions() as session:
            session.add(
                TelegramDeskInvite(
                    token=token,
                    tenant_id=tenant_id,
                    expires_at=expires_at,
                    used_at=None,
                    created_at=now,
                )
            )
            await session.commit()
        return {
            "token": token,
            "tenant_id": tenant_id,
            "expires_at": expires_at.isoformat(),
            "invite_link": f"https://t.me/{bot_username}?start={token}",
        }

    async def latest_open_telegram_invite(
        self, tenant_id: str, *, bot_username: str = "fivecent_test_bot"
    ) -> Optional[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        async with self._sessions() as session:
            row = await session.scalar(
                select(TelegramDeskInvite)
                .where(
                    TelegramDeskInvite.tenant_id == tenant_id,
                    TelegramDeskInvite.used_at.is_(None),
                    TelegramDeskInvite.expires_at > now,
                )
                .order_by(TelegramDeskInvite.created_at.desc())
                .limit(1)
            )
        if row is None:
            return None
        return {
            "token": row.token,
            "tenant_id": row.tenant_id,
            "expires_at": row.expires_at.isoformat(),
            "invite_link": f"https://t.me/{bot_username}?start={row.token}",
        }

    async def consume_telegram_desk_invite(self, token: str) -> Optional[str]:
        """Mark invite used and return tenant_id, or None if invalid/expired/used."""
        cleaned = (token or "").strip()
        if not cleaned:
            return None
        now = datetime.now(timezone.utc)
        async with self._sessions() as session, session.begin():
            row = await session.scalar(
                select(TelegramDeskInvite)
                .where(TelegramDeskInvite.token == cleaned)
                .with_for_update()
            )
            if row is None or row.used_at is not None:
                return None
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= now:
                return None
            tenant = await session.get(Tenant, row.tenant_id)
            if tenant is None or not tenant.is_active:
                return None
            row.used_at = now
            return row.tenant_id
