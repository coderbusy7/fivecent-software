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


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("tenant_id", "channel", "user_channel_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    user_channel_id: Mapped[str] = mapped_column(String(128))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("tenant_id", "provider_message_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[Optional[str]] = mapped_column(Text)
    provider_message_id: Mapped[str] = mapped_column(String(256))
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
