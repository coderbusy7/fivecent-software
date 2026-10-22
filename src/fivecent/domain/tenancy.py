from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from typing import Literal, Optional
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
RoutingMode = Literal["broadcast", "round_robin"]
ROUTING_MODES = frozenset({"broadcast", "round_robin"})

DEFAULT_FEATURE_FLAGS = {
    "enable_instant_quote": True,
    "enable_soft_hold": True,
    "enable_after_hours_only": False,
    "enable_tandem_charter_upsell": True,
    "enable_perk_negotiation": True,
    "require_email_for_quote": False,
    "after_hours_start": "19:00",
    "after_hours_end": "08:00",
}


class TenantConfigError(ValueError):
    """Invalid tenant commercial or hours configuration."""


def _parse_hhmm(value: str) -> time:
    raw = (value or "").strip()
    parts = raw.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise TenantConfigError(f"Invalid HH:MM time value: {value!r}")
    hours, minutes = int(parts[0]), int(parts[1])
    if hours > 23 or minutes > 59:
        raise TenantConfigError(f"Invalid HH:MM time value: {value!r}")
    return time(hours, minutes)


def validate_rate(name: str, value: Decimal) -> Decimal:
    amount = Decimal(value)
    if amount < 0 or amount > 1:
        raise TenantConfigError(f"{name} must be between 0 and 1 inclusive.")
    return amount


def validate_routing_mode(value: str) -> RoutingMode:
    mode = (value or "").strip()
    if mode not in ROUTING_MODES:
        raise TenantConfigError(
            f"routing_mode must be one of: {', '.join(sorted(ROUTING_MODES))}."
        )
    return mode  # type: ignore[return-value]


def is_after_hours(
    now: datetime,
    start: str = "19:00",
    end: str = "08:00",
    tz=PARIS,
) -> bool:
    """Overnight window, e.g. 19:00–08:00 in Europe/Paris."""
    local = now.astimezone(tz).time()
    start_t = _parse_hhmm(start)
    end_t = _parse_hhmm(end)
    if start_t <= end_t:
        return start_t <= local < end_t
    return local >= start_t or local < end_t


@dataclass(frozen=True)
class FeatureFlags:
    enable_instant_quote: bool = True
    enable_soft_hold: bool = True
    enable_after_hours_only: bool = False
    enable_tandem_charter_upsell: bool = True
    enable_perk_negotiation: bool = True
    require_email_for_quote: bool = False
    after_hours_start: str = "19:00"
    after_hours_end: str = "08:00"

    @classmethod
    def from_mapping(cls, raw: Optional[dict]) -> FeatureFlags:
        data = {**DEFAULT_FEATURE_FLAGS, **(raw or {})}
        start = str(data.get("after_hours_start", "19:00"))
        end = str(data.get("after_hours_end", "08:00"))
        # Validate shape when loading so corrupt DB values surface clearly.
        try:
            _parse_hhmm(start)
            _parse_hhmm(end)
        except TenantConfigError:
            start = str(DEFAULT_FEATURE_FLAGS["after_hours_start"])
            end = str(DEFAULT_FEATURE_FLAGS["after_hours_end"])
        return cls(
            enable_instant_quote=bool(data.get("enable_instant_quote", True)),
            enable_soft_hold=bool(data.get("enable_soft_hold", True)),
            enable_after_hours_only=bool(data.get("enable_after_hours_only", False)),
            enable_tandem_charter_upsell=bool(data.get("enable_tandem_charter_upsell", True)),
            enable_perk_negotiation=bool(data.get("enable_perk_negotiation", True)),
            require_email_for_quote=bool(data.get("require_email_for_quote", False)),
            after_hours_start=start,
            after_hours_end=end,
        )

    def as_dict(self) -> dict:
        return {
            "enable_instant_quote": self.enable_instant_quote,
            "enable_soft_hold": self.enable_soft_hold,
            "enable_after_hours_only": self.enable_after_hours_only,
            "enable_tandem_charter_upsell": self.enable_tandem_charter_upsell,
            "enable_perk_negotiation": self.enable_perk_negotiation,
            "require_email_for_quote": self.require_email_for_quote,
            "after_hours_start": self.after_hours_start,
            "after_hours_end": self.after_hours_end,
        }


def validate_feature_flags(raw: Optional[dict]) -> dict:
    """Strict validation for admin writes — rejects bad hours instead of falling back."""
    data = {**DEFAULT_FEATURE_FLAGS, **(raw or {})}
    start = str(data.get("after_hours_start", "19:00"))
    end = str(data.get("after_hours_end", "08:00"))
    _parse_hhmm(start)
    _parse_hhmm(end)
    flags = FeatureFlags.from_mapping({**data, "after_hours_start": start, "after_hours_end": end})
    return flags.as_dict()


@dataclass(frozen=True)
class Broker:
    id: str
    name: str
    phone_number: str
    telegram_chat_id: Optional[str] = None
    role: str = "broker"
    is_active: bool = True


@dataclass(frozen=True)
class TenantContext:
    id: str
    agency_name: str
    home_port: str
    currency: str
    whatsapp_phone_number_id: str
    whatsapp_waba_id: str
    vat_rate: Decimal
    apa_rate: Decimal
    max_discount_pct: Decimal
    routing_mode: RoutingMode
    feature_flags: FeatureFlags
    brokers: tuple[Broker, ...] = field(default_factory=tuple)
    is_active: bool = True
    service_type: str = "yachting"
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    telegram_chat_id: Optional[str] = None

    @property
    def name(self) -> str:
        return self.agency_name

    @property
    def primary_broker(self) -> Optional[Broker]:
        active = self.active_brokers
        return active[0] if active else None

    @property
    def broker_contact(self) -> str:
        active = self.active_brokers
        if not active:
            return "Broker desk"
        return ", ".join(f"{b.name} ({b.phone_number})" for b in active)

    @property
    def active_brokers(self) -> tuple[Broker, ...]:
        return tuple(b for b in self.brokers if b.is_active)

    def bot_should_run(self, now: datetime) -> bool:
        if not self.is_active:
            return False
        if not self.feature_flags.enable_after_hours_only:
            return True
        try:
            return is_after_hours(
                now,
                self.feature_flags.after_hours_start,
                self.feature_flags.after_hours_end,
            )
        except TenantConfigError:
            return False


def pick_alert_brokers(tenant: TenantContext, round_robin_index: int = 0) -> tuple[Broker, ...]:
    active = tenant.active_brokers
    if not active:
        return ()
    if tenant.routing_mode == "round_robin":
        return (active[round_robin_index % len(active)],)
    return active


def telegram_dispatch_chat_ids(tenant: TenantContext, brokers: tuple[Broker, ...] | None = None) -> list[str]:
    """Prefer tenant desk chat id; else active broker chat ids."""
    ids: list[str] = []
    desk = (tenant.telegram_chat_id or "").strip()
    if desk:
        ids.append(desk)
        return ids
    selected = brokers if brokers is not None else tenant.active_brokers
    for broker in selected:
        chat = (broker.telegram_chat_id or "").strip()
        if chat and chat not in ids:
            ids.append(chat)
    return ids
