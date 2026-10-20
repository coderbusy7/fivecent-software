from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import uuid4

from fivecent.application.ports import Container
from fivecent.domain.fleet import check_fleet_availability
from fivecent.domain.holds import SoftHoldRequest, escalate_and_soft_hold
from fivecent.domain.negotiation import evaluate_negotiation_floor
from fivecent.domain.quoting import DomainError, calculate_itemized_quote, quote_to_dict
from fivecent.domain.tenancy import TenantContext, pick_alert_brokers


def tool_schemas(boat_ids: list[str], tenant: TenantContext | None = None) -> list[dict]:
    vat = tenant.vat_rate if tenant else Decimal("0.20")
    apa = tenant.apa_rate if tenant else Decimal("0.30")
    discount = tenant.max_discount_pct if tenant else Decimal("0.08")
    boat_enum: dict[str, Any] = {"type": "string"}
    if boat_ids:
        boat_enum["enum"] = boat_ids
    return [
        {
            "type": "function",
            "function": {
                "name": "check_fleet_availability",
                "description": (
                    "Find boats that can take this charter. Hard-rejects more than 12 passengers."
                ),
                "parameters": {
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
            },
        },
        {
            "type": "function",
            "function": {
                "name": "calculate_itemized_quote",
                "description": (
                    f"Deterministic quote using this agency's VAT {vat} and APA {apa}. "
                    "Never invent totals in prose; always call this tool."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "boat_id": boat_enum,
                        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                        "days": {"type": "integer", "minimum": 1, "default": 1},
                        "skipper": {"type": "boolean", "default": True},
                        "email": {"type": "string"},
                    },
                    "required": ["boat_id", "start_date"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "evaluate_negotiation_floor",
                "description": (
                    "Check a client budget against an existing quote_id. "
                    f"Max {discount} off base; VAT and APA are frozen."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "quote_id": {"type": "string"},
                        "proposed_budget": {"type": "number"},
                    },
                    "required": ["quote_id", "proposed_budget"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "escalate_and_soft_hold",
                "description": (
                    "Place a 24h option. Requires customer name and email. "
                    "Does not confirm a booking."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string"},
                        "email": {"type": "string"},
                        "boat_id": boat_enum,
                        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                        "days": {"type": "integer", "minimum": 1, "default": 1},
                        "quote_id": {"type": "string"},
                    },
                    "required": ["customer_name", "email", "boat_id", "start_date"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _error(code: str, message: str) -> str:
    return json.dumps({"ok": False, "code": code, "error": message})


def _parse_date(value: Any) -> date:
    if not isinstance(value, str):
        raise ValueError("start_date must be YYYY-MM-DD.")
    return date.fromisoformat(value)


async def dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    container: Container,
    tenant: TenantContext,
    conversation_id: str,
) -> str:
    now = datetime.now(timezone.utc)
    tenant_id = tenant.id
    if name == "check_fleet_availability":
        pax = arguments.get("pax")
        days = arguments.get("days", 1)
        length = arguments.get("length_m")
        length_m = Decimal(str(length)) if length is not None else None
        boats = await container.fleet.list_active(tenant_id)
        holds = await container.holds.list_active(tenant_id, now)
        result = check_fleet_availability(
            boats,
            holds,
            pax=pax,
            start_date=_parse_date(arguments.get("start_date")),
            days=days,
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

    if name == "calculate_itemized_quote":
        if not tenant.feature_flags.enable_instant_quote:
            return _error("feature_disabled", "Instant quotes are disabled for this agency.")
        if tenant.feature_flags.require_email_for_quote:
            email = str(arguments.get("email") or "")
            if "@" not in email:
                return _error("email_required", "This agency requires an email before quoting.")
        boat = await container.fleet.get(tenant_id, arguments.get("boat_id"))
        if boat is None:
            available = [b.id for b in await container.fleet.list_active(tenant_id)]
            return _error("unknown_boat", f"Unknown boat. Available: {available}")
        quote = calculate_itemized_quote(
            boat,
            _parse_date(arguments.get("start_date")),
            int(arguments.get("days", 1)),
            skipper=bool(arguments.get("skipper", True)),
            vat_rate=tenant.vat_rate,
            apa_rate=tenant.apa_rate,
            currency=tenant.currency,  # type: ignore[arg-type]
        )
        if isinstance(quote, DomainError):
            return _error(quote.code, quote.message)
        quote_id = str(uuid4())
        await container.quotes.save(
            tenant_id=tenant_id, conversation_id=conversation_id, quote_id=quote_id, quote=quote
        )
        return json.dumps({"ok": True, **quote_to_dict(quote, quote_id)})

    if name == "evaluate_negotiation_floor":
        quote = await container.quotes.get(tenant_id, arguments.get("quote_id"))
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
            payload = {
                "ok": False,
                "code": result.error.code,
                "error": result.error.message,
                "floor_total": str(result.floor_total) if result.floor_total is not None else None,
            }
            return json.dumps(payload)
        return json.dumps({
            "ok": True,
            "accepted_total": str(result.accepted_total),
            "concession": str(result.concession_on_base),
            "floor_total": str(result.floor_total),
            "currency": quote.currency,
        })

    if name == "escalate_and_soft_hold":
        if not tenant.feature_flags.enable_soft_hold:
            return _error("feature_disabled", "24h options are disabled for this agency.")
        boat_id = arguments.get("boat_id")
        boat = await container.fleet.get(tenant_id, boat_id)
        if boat is None:
            return _error("unknown_boat", "Unknown boat.")
        holds = await container.holds.list_active(tenant_id, now)
        approval = escalate_and_soft_hold(
            SoftHoldRequest(
                customer_name=str(arguments.get("customer_name", "")),
                email=str(arguments.get("email", "")),
                boat_id=str(boat_id),
                start_date=_parse_date(arguments.get("start_date")),
                days=int(arguments.get("days", 1)),
            ),
            holds,
            now=now,
        )
        if isinstance(approval, DomainError):
            return _error(approval.code, approval.message)
        quote_id: Optional[str] = arguments.get("quote_id")
        if quote_id:
            existing_quote = await container.quotes.get(tenant_id, quote_id)
            if existing_quote is None:
                return _error("unknown_quote", "Unknown quote_id. Calculate a quote first.")
        lead_id = await container.leads.upsert_hold_lead(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            customer_name=approval.customer_name,
            email=approval.email,
            boat_id=approval.boat_id,
            start_date=approval.start_date,
            days=approval.days,
            quote_id=quote_id,
        )
        await container.holds.create(
            tenant_id=tenant_id,
            boat_id=approval.boat_id,
            start_date=approval.start_date,
            days=approval.days,
            expires_at=approval.expires_at,
            lead_id=lead_id,
        )
        payload = {
            "ok": True,
            "lead_id": lead_id,
            "boat_id": approval.boat_id,
            "start_date": approval.start_date.isoformat(),
            "days": approval.days,
            "expires_at": approval.expires_at.isoformat(),
            "state": "option_held",
        }
        rr = await container.cache.incr(f"rr:{tenant_id}") if tenant.routing_mode == "round_robin" else 0
        selected = pick_alert_brokers(tenant, rr)
        payload["alert_brokers"] = [
            {"name": b.name, "phone_number": b.phone_number, "telegram_chat_id": b.telegram_chat_id}
            for b in selected
        ]
        await container.alerts.notify_hold(tenant_id, payload)
        return json.dumps(payload)

    return _error("unknown_tool", f"Unknown tool: {name}")
