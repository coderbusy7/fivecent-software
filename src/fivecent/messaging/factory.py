from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fivecent.application.registry import PluginRegistry
from fivecent.config import Settings
from fivecent.messaging.graph import WebhookAdapter
from fivecent.messaging.outbound import ChannelSenderRegistry, TestChannelSender
from fivecent.messaging.plugin_instagram import InstagramWebhookAdapter
from fivecent.messaging.plugin_meta import MetaMessengerWebhookAdapter
from fivecent.messaging.plugin_whatsapp import WhatsAppSender, WhatsAppWebhookAdapter


def build_inbound_plugins(settings: Settings) -> PluginRegistry[WebhookAdapter]:
    secret = settings.whatsapp_app_secret
    verify = settings.whatsapp_verify_token
    return PluginRegistry({
        "whatsapp": WhatsAppWebhookAdapter(secret, verify),
        "instagram": InstagramWebhookAdapter("", ""),
        "messenger": MetaMessengerWebhookAdapter("", ""),
    })


def build_outbound_plugins(
    settings: Settings,
    closers: list[Callable],
    *,
    sessions: Optional[async_sessionmaker[AsyncSession]] = None,
) -> ChannelSenderRegistry:
    channels: dict = {"test": TestChannelSender()}
    if settings.whatsapp_access_token:

        async def resolve_phone_number_id(tenant_id: str) -> Optional[str]:
            if sessions is None:
                return settings.whatsapp_phone_number_id or None
            from sqlalchemy import select

            from fivecent.adapters.persistence.models import Tenant

            async with sessions() as session:
                result = await session.execute(
                    select(Tenant.whatsapp_phone_number_id).where(Tenant.id == tenant_id)
                )
                phone = result.scalar_one_or_none()
            return phone or settings.whatsapp_phone_number_id or None

        whatsapp = WhatsAppSender(
            access_token=settings.whatsapp_access_token,
            phone_number_id=settings.whatsapp_phone_number_id,
            graph_version=settings.whatsapp_graph_version,
            resolve_phone_number_id=resolve_phone_number_id,
        )
        closers.append(whatsapp.aclose)
        channels["whatsapp"] = whatsapp
    return ChannelSenderRegistry(channels)
