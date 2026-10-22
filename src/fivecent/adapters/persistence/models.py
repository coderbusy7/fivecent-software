from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agency_name: Mapped[str] = mapped_column(String(128))
    home_port: Mapped[str] = mapped_column(String(64), default="Port Vauban")
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    whatsapp_phone_number_id: Mapped[str] = mapped_column(String(64), unique=True)
    whatsapp_waba_id: Mapped[str] = mapped_column(String(64), default="")
    instagram_account_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    messenger_page_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    default_vat_rate: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("0.20"))
    default_apa_rate: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("0.30"))
    max_discount_pct: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("0.08"))
    service_type: Mapped[str] = mapped_column(String(64), default="yachting")
    llm_provider: Mapped[str] = mapped_column(String(64), default="openai")
    llm_model: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini")
    routing_mode: Mapped[str] = mapped_column(String(32), default="broadcast")
    feature_flags: Mapped[dict] = mapped_column(JSON, default=dict)
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Broker(Base):
    __tablename__ = "brokers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    phone_number: Mapped[str] = mapped_column(String(32))
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32), default="broker")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TelegramDeskInvite(Base):
    """Single-use, expiring deep-link token for desk Telegram onboarding."""

    __tablename__ = "telegram_desk_invites"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("tenant_id", "channel", "user_channel_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    user_channel_id: Mapped[str] = mapped_column(String(128))
    assigned_broker_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    handoff_status: Mapped[str] = mapped_column(String(32), default="open")
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    tenant_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    api_token_hash: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    actor_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    before_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TenantUsageDaily(Base):
    __tablename__ = "tenant_usage_daily"
    __table_args__ = (UniqueConstraint("tenant_id", "day"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    day: Mapped[str] = mapped_column(String(10))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("tenant_id", "provider_message_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[Optional[str]] = mapped_column(Text)
    provider_message_id: Mapped[str] = mapped_column(String(256))
    # received | processing | completed | failed | skipped
    processing_status: Mapped[str] = mapped_column(String(32), default="completed")
    skip_reason: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InboundPayload(Base):
    __tablename__ = "inbound_payloads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    # accepted | failed | replayed
    status: Mapped[str] = mapped_column(String(32), default="accepted")
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
