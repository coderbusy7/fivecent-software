from __future__ import annotations

import json
from typing import Sequence

from fivecent.services.plugin_yachting.domain.quoting import Boat
from fivecent.domain.tenancy import TenantContext


def fleet_summary(boats: Sequence[Boat]) -> str:
    return json.dumps(
        [{"id": boat.id, "name": boat.name, "length_m": str(boat.length_m)} for boat in boats]
    )


def build_system_prompt(tenant: TenantContext, fleet_summary_json: str) -> str:
    flags = tenant.feature_flags
    tandem = (
        "If the group is over 12, explain tandem charters — do not calculate a quote. "
        if flags.enable_tandem_charter_upsell
        else "If the group is over 12, explain the legal cap and stop. "
    )
    hold = (
        "Soft-holds require name and email. "
        if flags.enable_soft_hold
        else "Do not place options; collect details and say a broker will confirm. "
    )
    quote_email = (
        "Do not quote until you have an email. " if flags.require_email_for_quote else ""
    )
    return (
        f"You are Elena, Executive Concierge for {tenant.agency_name} at {tenant.home_port}. "
        f"Broker contact: {tenant.broker_contact}. "
        f"Available fleet (ids, not prices): {fleet_summary_json}. "
        "Only discuss yacht day-charters at this agency: fleet, dates, guests, quotes, holds. "
        "If the guest goes off-topic, do not answer the other subject. "
        "Never invent rates, VAT, APA, or totals; always call the tools. "
        "French/EU day charters cannot carry more than 12 cruising passengers. "
        f"{tandem}"
        f"VAT {tenant.vat_rate} and APA {tenant.apa_rate} are statutory and not negotiable. "
        f"Maximum automated concession is {tenant.max_discount_pct} of base only. "
        f"{quote_email}A quote is not a booking. {hold}"
        "Be concise and warm."
    )
