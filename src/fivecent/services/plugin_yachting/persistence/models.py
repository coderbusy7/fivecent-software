from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from fivecent.adapters.persistence.models import Base


class FleetInventory(Base):
    __tablename__ = "fleet_inventory"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    length_m: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    capacity: Mapped[int] = mapped_column(Integer)
    low_season_daily_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    high_season_daily_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    skipper_daily_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    boat_id: Mapped[str] = mapped_column(ForeignKey("fleet_inventory.id"))
    start_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer)
    season: Mapped[str] = mapped_column(String(8))
    skipper_included: Mapped[bool] = mapped_column(Boolean)
    daily_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    base: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    skipper: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    vat: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    apa: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    policy_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    customer_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    boat_id: Mapped[Optional[str]] = mapped_column(ForeignKey("fleet_inventory.id"))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    days: Mapped[Optional[int]] = mapped_column(Integer)
    quote_id: Mapped[Optional[str]] = mapped_column(ForeignKey("quotes.id"))
    state: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Hold(Base):
    __tablename__ = "holds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    boat_id: Mapped[str] = mapped_column(ForeignKey("fleet_inventory.id"))
    start_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
