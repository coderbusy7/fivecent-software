from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import uuid4

from fivecent.application.ports import CachePort
from fivecent.application.tools import Tool, ToolContext, ToolSpec
from fivecent.services.plugin_yachting.domain.fleet import check_fleet_availability
from fivecent.services.plugin_yachting.domain.holds import SoftHoldRequest, escalate_and_soft_hold
from fivecent.services.plugin_yachting.domain.negotiation import evaluate_negotiation_floor
from fivecent.services.plugin_yachting.domain.quoting import (
    DomainError,
    calculate_itemized_quote,
    quote_to_dict,
)
from fivecent.domain.tenancy import TenantContext, pick_alert_brokers, telegram_dispatch_chat_ids
from fivecent.services.plugin_yachting.ports import (
    BrokerAlertPort,
    FleetRepo,
    HoldRepo,
    LeadRepo,
    QuoteRepo,
)


class YachtingTools:
    def __init__(
        self,
        *,
        fleet: FleetRepo,
        holds: HoldRepo,
        quotes: QuoteRepo,
        leads: LeadRepo,
        cache: CachePort,
        alerts: BrokerAlertPort,
    ) -> None:
        self._fleet = fleet
        self._holds = holds
        self._quotes = quotes
        self._leads = leads
        self._cache = cache
        self._alerts = alerts

    def registry(self, boat_ids: list[str], tenant: TenantContext) -> list[Tool]:
        boat_schema: dict[str, Any] = {"type": "string"}
        if boat_ids:
            boat_schema["enum"] = boat_ids
        vat = tenant.vat_rate
        apa = tenant.apa_rate
        discount = tenant.max_discount_pct
        return [
            Tool(
                ToolSpec(
                    name="check_fleet_availability",
                    description=(
                        "Find boats that can take this charter. Hard-rejects more than 12 passengers."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "pax": {"type": "integer", "minimum": 1},
                            "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "days": {"type": "integer", "minimum": 1, "default": 1},
                            "length_m": {"type": "number"},
                        },
                        "required": ["pax", "start_date"],
                        "additionalProperties": False,
                    },
                ),
                self.check_fleet_availability,
            ),
            Tool(
                ToolSpec(
                    name="calculate_itemized_quote",
                    description=(
                        f"Deterministic quote using this agency's VAT {vat} and APA {apa}. "
                        "Never invent totals in prose; always call this tool."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "boat_id": boat_schema,
                            "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "days": {"type": "integer", "minimum": 1, "default": 1},
                            "skipper": {"type": "boolean", "default": True},
                            "email": {"type": "string"},
                        },
                        "required": ["boat_id", "start_date"],
                        "additionalProperties": False,
                    },
                ),
                self.calculate_itemized_quote,
            ),
            Tool(
                ToolSpec(
                    name="evaluate_negotiation_floor",
                    description=(
                        "Check a client budget against an existing quote_id. "
                        f"Max {discount} off base; VAT and APA are frozen."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "quote_id": {"type": "string"},
                            "proposed_budget": {"type": "number"},
                        },
                        "required": ["quote_id", "proposed_budget"],
                        "additionalProperties": False,
                    },
                ),
                self.evaluate_negotiation_floor,
            ),
            Tool(
                ToolSpec(
                    name="request_broker_followup",
                    description=(
                        "Ask the desk to WhatsApp or call the guest later about a destination "
                        "or fleet we cannot confirm from tools (for example Monaco repositioning). "
                        "Does not quote and does not book."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "destination": {"type": "string"},
                            "contact_preference": {
                                "type": "string",
                                "enum": ["whatsapp", "call", "either"],
                            },
                            "notes": {"type": "string"},
                            "customer_name": {"type": "string"},
                        },
                        "required": ["destination", "contact_preference"],
                        "additionalProperties": False,
                    },
                ),
                self.request_broker_followup,
            ),
            Tool(
                ToolSpec(
                    name="escalate_and_soft_hold",
                    description=(
                        "Place a 24h option. Requires customer name and email. "
                        "Does not confirm a booking."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "customer_name": {"type": "string"},
                            "email": {"type": "string"},
                            "boat_id": boat_schema,
                            "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "days": {"type": "integer", "minimum": 1, "default": 1},
                            "quote_id": {"type": "string"},
                        },
                        "required": ["customer_name", "email", "boat_id", "start_date"],
                        "additionalProperties": False,
                    },
                ),
                self.escalate_and_soft_hold,
            ),
        ]

    async def check_fleet_availability(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> str:
        now = datetime.now(timezone.utc)
        tenant = context.tenant
        pax = arguments.get("pax")
        if type(pax) is not int or pax < 1:
            return _error("invalid_pax", "Passenger count must be a positive integer.")
        length = arguments.get("length_m")
        try:
            length_m = Decimal(str(length)) if length is not None else None
        except InvalidOperation:
            return _error("invalid_length", "length_m must be a positive number.")
        if length_m is not None and (not length_m.is_finite() or length_m <= 0):
            return _error("invalid_length", "length_m must be a positive number.")
        boats = await self._fleet.list_active(tenant.id)
        holds = await self._holds.list_active(tenant.id, now)
        result = check_fleet_availability(
            boats,
            holds,
            pax=pax,
            start_date=_parse_date(arguments.get("start_date")),
            days=arguments.get("days", 1),
            length_m=length_m,
            now=now,
            suggest_tandem=tenant.feature_flags.enable_tandem_charter_upsell,
        )
        if not result.ok and result.error:
            return _error(result.error.code, result.error.message)
        return json.dumps({
            "ok": True,
            "boats": [
                {
                    "boat_id": item.boat.id,
                    "name": item.boat.name,
                    "length_m": str(item.boat.length_m),
                    "capacity": item.boat.capacity,
                }
                for item in result.boats
            ],
        })

    async def calculate_itemized_quote(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> str:
        tenant = context.tenant
        if not tenant.feature_flags.enable_instant_quote:
            return _error("feature_disabled", "Instant quotes are disabled for this agency.")
        if tenant.feature_flags.require_email_for_quote:
            email = str(arguments.get("email") or "")
            if "@" not in email:
                return _error("email_required", "This agency requires an email before quoting.")
        boat_id = arguments.get("boat_id")
        if not isinstance(boat_id, str):
            return _error("unknown_boat", "Unknown boat.")
        days = arguments.get("days", 1)
        if type(days) is not int or days < 1:
            return _error("invalid_days", "days must be a positive integer.")
        skipper = arguments.get("skipper", True)
        if not isinstance(skipper, bool):
            return _error("invalid_skipper", "skipper must be a boolean.")
        boat = await self._fleet.get(tenant.id, boat_id)
        if boat is None:
            available = [item.id for item in await self._fleet.list_active(tenant.id)]
            return _error("unknown_boat", f"Unknown boat. Available: {available}")
        quote = calculate_itemized_quote(
            boat,
            _parse_date(arguments.get("start_date")),
            days,
            skipper=skipper,
            vat_rate=tenant.vat_rate,
            apa_rate=tenant.apa_rate,
            currency=tenant.currency,  # type: ignore[arg-type]
        )
        if isinstance(quote, DomainError):
            return _error(quote.code, quote.message)
        quote_id = str(uuid4())
        await self._quotes.save(
            tenant_id=tenant.id,
            conversation_id=context.conversation_id,
            quote_id=quote_id,
            quote=quote,
        )
        return json.dumps({"ok": True, **quote_to_dict(quote, quote_id)})

    async def evaluate_negotiation_floor(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> str:
        tenant = context.tenant
        quote_id = arguments.get("quote_id")
        if not isinstance(quote_id, str):
            return _error("unknown_quote", "Unknown quote_id. Calculate a quote first.")
        quote = await self._quotes.get(tenant.id, quote_id)
        if quote is None:
            return _error("unknown_quote", "Unknown quote_id. Calculate a quote first.")
        try:
            budget = Decimal(str(arguments.get("proposed_budget")))
        except (InvalidOperation, TypeError):
            return _error("invalid_budget", "proposed_budget must be a number.")
        result = evaluate_negotiation_floor(
            quote,
            budget,
            max_discount_pct=tenant.max_discount_pct,
            offer_perks=tenant.feature_flags.enable_perk_negotiation,
        )
        if not result.ok and result.error:
            return json.dumps({
                "ok": False,
                "code": result.error.code,
                "error": result.error.message,
                "floor_total": (
                    str(result.floor_total) if result.floor_total is not None else None
                ),
            })
        return json.dumps({
            "ok": True,
            "accepted_total": str(result.accepted_total),
            "concession": str(result.concession_on_base),
            "floor_total": str(result.floor_total),
            "currency": quote.currency,
        })

    async def request_broker_followup(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> str:
        destination = str(arguments.get("destination") or "").strip()
        preference = str(arguments.get("contact_preference") or "").strip()
        if not destination:
            return _error("invalid_destination", "destination is required.")
        if preference not in {"whatsapp", "call", "either"}:
            return _error("invalid_preference", "contact_preference must be whatsapp, call, or either.")
        tenant = context.tenant
        payload = {
            "ok": True,
            "reason": "followup",
            "agency_name": tenant.agency_name,
            "destination": destination,
            "contact_preference": preference,
            "notes": str(arguments.get("notes") or "").strip(),
            "customer_name": str(arguments.get("customer_name") or "").strip(),
            "conversation_id": context.conversation_id,
            **_guest_alert_fields(context),
            **await self._alert_routing_fields(tenant, context),
        }
        await self._alerts.notify_followup(tenant.id, payload)
        return json.dumps(payload)

    async def escalate_and_soft_hold(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> str:
        tenant = context.tenant
        if not tenant.feature_flags.enable_soft_hold:
            return _error("feature_disabled", "24h options are disabled for this agency.")
        boat_id = arguments.get("boat_id")
        if not isinstance(boat_id, str):
            return _error("unknown_boat", "Unknown boat.")
        days = arguments.get("days", 1)
        if type(days) is not int or days < 1:
            return _error("invalid_days", "days must be a positive integer.")
        boat = await self._fleet.get(tenant.id, boat_id)
        if boat is None:
            return _error("unknown_boat", "Unknown boat.")
        now = datetime.now(timezone.utc)
        holds = await self._holds.list_active(tenant.id, now)
        approval = escalate_and_soft_hold(
            SoftHoldRequest(
                customer_name=str(arguments.get("customer_name", "")),
                email=str(arguments.get("email", "")),
                boat_id=str(boat_id),
                start_date=_parse_date(arguments.get("start_date")),
                days=days,
            ),
            holds,
            now=now,
        )
        if isinstance(approval, DomainError):
            return _error(approval.code, approval.message)
        quote_id: Optional[str] = arguments.get("quote_id")
        if quote_id and await self._quotes.get(tenant.id, quote_id) is None:
            return _error("unknown_quote", "Unknown quote_id. Calculate a quote first.")
        lead_id = await self._leads.upsert_hold_lead(
            tenant_id=tenant.id,
            conversation_id=context.conversation_id,
            customer_name=approval.customer_name,
            email=approval.email,
            boat_id=approval.boat_id,
            start_date=approval.start_date,
            days=approval.days,
            quote_id=quote_id,
        )
        reserved = await self._holds.create_if_available(
            tenant_id=tenant.id,
            boat_id=approval.boat_id,
            start_date=approval.start_date,
            days=approval.days,
            now=now,
            expires_at=approval.expires_at,
            lead_id=lead_id,
        )
        if not reserved:
            return _error("hold_conflict", "That boat already has an active option on those dates.")
        quote_total = None
        currency = tenant.currency
        if quote_id:
            quote = await self._quotes.get(tenant.id, quote_id)
            if quote is not None:
                quote_total = str(quote.total)
                currency = quote.currency
        payload = {
            "ok": True,
            "lead_id": lead_id,
            "agency_name": tenant.agency_name,
            "customer_name": approval.customer_name,
            "email": approval.email,
            "boat_id": approval.boat_id,
            "boat_name": boat.name,
            "start_date": approval.start_date.isoformat(),
            "days": approval.days,
            "expires_at": approval.expires_at.isoformat(),
            "state": "option_held",
            "quote_id": quote_id,
            "quote_total": quote_total,
            "currency": currency,
            **_guest_alert_fields(context),
            **await self._alert_routing_fields(tenant, context),
        }
        await self._alerts.notify_hold(tenant.id, payload)
        return json.dumps(payload)

    async def _selected_alert_brokers(self, tenant: TenantContext) -> list[dict[str, Any]]:
        rr = (
            await self._cache.incr(f"rr:{tenant.id}")
            if tenant.routing_mode == "round_robin"
            else 0
        )
        selected = pick_alert_brokers(tenant, rr)
        return [
            {
                "name": broker.name,
                "phone_number": broker.phone_number,
                "telegram_chat_id": broker.telegram_chat_id,
            }
            for broker in selected
        ]

    async def _alert_routing_fields(
        self, tenant: TenantContext, context: ToolContext
    ) -> dict[str, Any]:
        brokers = await self._selected_alert_brokers(tenant)
        guest = _guest_digits(context)
        fields: dict[str, Any] = {
            "alert_brokers": brokers,
            "tenant_telegram_chat_id": tenant.telegram_chat_id,
            "dispatch_chat_ids": telegram_dispatch_chat_ids(tenant),
        }
        if guest:
            fields["mute_command"] = f"/mute_{guest}_{tenant.id}"
        return fields


def _guest_digits(context: ToolContext) -> str:
    if context.channel != "whatsapp" or not context.user_channel_id:
        return ""
    return "".join(ch for ch in context.user_channel_id if ch.isdigit())


def _guest_alert_fields(context: ToolContext) -> dict[str, str]:
    fields: dict[str, str] = {
        "guest_channel": context.channel,
        "guest_user_id": context.user_channel_id,
    }
    digits = _guest_digits(context)
    if digits:
        fields["wa_me"] = f"https://wa.me/{digits}"
    return fields


def _error(code: str, message: str) -> str:
    return json.dumps({"ok": False, "code": code, "error": message})


def _parse_date(value: Any) -> date:
    if not isinstance(value, str):
        raise ValueError("start_date must be YYYY-MM-DD.")
    return date.fromisoformat(value)
