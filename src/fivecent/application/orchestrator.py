from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from fivecent.application.ports import Container, LLMResult
from fivecent.domain.tenancy import TenantContext
from fivecent.application.tools import dispatch_tool, tool_schemas

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    reply: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    tools: list[str]


def build_system_prompt(tenant: TenantContext, fleet_summary: str) -> str:
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
        f"Available fleet (ids, not prices): {fleet_summary}. "
        "Never invent rates, VAT, APA, or totals; always call the tools. "
        "French/EU day charters cannot carry more than 12 cruising passengers. "
        f"{tandem}"
        f"VAT {tenant.vat_rate} and APA {tenant.apa_rate} are statutory and not negotiable. "
        f"Maximum automated concession is {tenant.max_discount_pct} of base only. "
        f"{quote_email}A quote is not a booking. {hold}"
        "Be concise and warm."
    )


async def orchestrate(
    *,
    container: Container,
    tenant: TenantContext,
    conversation_id: str,
    history: list[dict],
    user_text: str,
) -> OrchestrationResult:
    started = time.perf_counter()
    boats = await container.fleet.list_active(tenant.id)
    boat_ids = [boat.id for boat in boats]
    summary = json.dumps([{"id": b.id, "name": b.name, "length_m": str(b.length_m)} for b in boats])
    tools = tool_schemas(boat_ids, tenant)
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(tenant, summary)},
        *history,
        {"role": "user", "content": user_text},
    ]
    tokens_in = 0
    tokens_out = 0
    used: list[str] = []

    first: LLMResult = await container.llm.complete(messages, tools, tool_choice="auto")
    tokens_in += first.tokens_in
    tokens_out += first.tokens_out
    message_content = first.content

    if first.tool_calls:
        messages.append({
            "role": "assistant",
            "content": first.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in first.tool_calls
            ],
        })
        for call in first.tool_calls:
            used.append(call.name)
            try:
                arguments = json.loads(call.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object.")
                result = await dispatch_tool(
                    call.name,
                    arguments,
                    container=container,
                    tenant=tenant,
                    conversation_id=conversation_id,
                )
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                logger.warning("Tool execution rejected: %s", exc)
                result = json.dumps({"ok": False, "error": str(exc)})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        second = await container.llm.complete(messages, tools, tool_choice="none")
        tokens_in += second.tokens_in
        tokens_out += second.tokens_out
        message_content = second.content

    if not message_content:
        raise RuntimeError("OpenAI returned no text reply.")
    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "turn complete tenant=%s tools=%s tokens_in=%s tokens_out=%s latency_ms=%s",
        tenant.id,
        used,
        tokens_in,
        tokens_out,
        latency_ms,
    )
    return OrchestrationResult(
        reply=message_content,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        tools=used,
    )
