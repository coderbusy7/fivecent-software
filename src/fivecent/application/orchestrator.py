from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from fivecent.application.ports import ChatMessage, Container, LLMResult
from fivecent.domain.tenancy import TenantContext
from fivecent.application.tools import ToolContext

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    reply: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    tools: list[str]


async def orchestrate(
    *,
    container: Container,
    tenant: TenantContext,
    conversation_id: str,
    history: list[dict],
    user_text: str,
    channel: str,
    user_channel_id: str,
) -> OrchestrationResult:
    started = time.perf_counter()
    service_module = container.services.get(tenant.service_type)
    provider = container.providers.get(tenant.llm_provider)
    service = await service_module.prepare(tenant)
    if service.guard is not None:
        canned = await service.guard.evaluate(
            tenant=tenant,
            history=history,
            user_text=user_text,
            conversation_id=conversation_id,
            channel=channel,
            user_channel_id=user_channel_id,
        )
        if canned:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return OrchestrationResult(
                reply=canned,
                tokens_in=0,
                tokens_out=0,
                latency_ms=latency_ms,
                tools=["scope_guardrail"],
            )
    tool_context = ToolContext(tenant=tenant, conversation_id=conversation_id)
    messages = [
        ChatMessage(role="system", content=service.system_prompt),
        *[
            ChatMessage(role=turn["role"], content=turn["content"])
            for turn in history
        ],
        ChatMessage(role="user", content=user_text),
    ]
    tokens_in = 0
    tokens_out = 0
    used: list[str] = []

    tool_specs = service.tools.specs()
    first: LLMResult = await provider.complete(
        messages,
        tool_specs,
        model=tenant.llm_model,
        tool_choice="auto",
    )
    tokens_in += first.tokens_in
    tokens_out += first.tokens_out
    message_content = first.content

    if first.tool_calls:
        messages.append(
            ChatMessage(
                role="assistant",
                content=first.content,
                tool_calls=tuple(first.tool_calls),
            )
        )
        for call in first.tool_calls:
            used.append(call.name)
            result = await service.tools.execute(call, tool_context)
            messages.append(
                ChatMessage(role="tool", tool_call_id=call.id, content=result)
            )

        second = await provider.complete(
            messages,
            tool_specs,
            model=tenant.llm_model,
            tool_choice="none",
        )
        tokens_in += second.tokens_in
        tokens_out += second.tokens_out
        message_content = second.content

    if not message_content:
        raise RuntimeError("LLM provider returned no text reply.")
    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "turn complete tenant=%s provider=%s model=%s tools=%s tokens_in=%s tokens_out=%s latency_ms=%s",
        tenant.id,
        tenant.llm_provider,
        tenant.llm_model,
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
