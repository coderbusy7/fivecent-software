from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.adapters.persistence.models import Broker, Tenant
from fivecent.config import Settings
from fivecent.domain.tenancy import DEFAULT_FEATURE_FLAGS
from fivecent.services.yachting.persistence.seed import seed_fleet

SEED_TENANT_ID = "port-vauban"


async def seed_if_empty(sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    async with sessions() as session:
        existing = await session.get(Tenant, settings.seed_tenant_id)
        if existing is None:
            session.add(
                Tenant(
                    id=settings.seed_tenant_id,
                    agency_name="Port Vauban Concierge",
                    home_port="Port Vauban, Antibes",
                    currency="EUR",
                    whatsapp_phone_number_id=settings.whatsapp_phone_number_id or "seed-phone-number-id",
                    whatsapp_waba_id="seed-waba-id",
                    default_vat_rate=Decimal("0.20"),
                    default_apa_rate=Decimal("0.30"),
                    max_discount_pct=Decimal("0.08"),
                    service_type="yachting",
                    llm_provider="openai",
                    llm_model=settings.openai_model,
                    routing_mode="broadcast",
                    feature_flags=dict(DEFAULT_FEATURE_FLAGS),
                    is_active=True,
                    created_at=datetime.now(timezone.utc),
                )
            )
        elif settings.whatsapp_phone_number_id:
            existing.whatsapp_phone_number_id = settings.whatsapp_phone_number_id

        brokers = await session.execute(
            select(Broker).where(Broker.tenant_id == settings.seed_tenant_id)
        )
        if brokers.scalars().first() is None:
            session.add(
                Broker(
                    id="broker-elena-desk",
                    tenant_id=settings.seed_tenant_id,
                    name="Elena desk",
                    phone_number="+33600000000",
                    role="owner",
                    is_active=True,
                    created_at=datetime.now(timezone.utc),
                )
            )

        await seed_fleet(session, settings.seed_tenant_id)
        await session.commit()
