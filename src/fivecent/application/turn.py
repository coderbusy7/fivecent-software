from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Optional
from uuid import uuid4

from fivecent.application.orchestrator import orchestrate
from fivecent.application.ports import CachePort, Container, LockBusy
from fivecent.domain.events import InboundMessageEvent
from fivecent.domain.tenancy import TenantContext, pick_alert_brokers
from fivecent.messaging.errors import SessionWindowClosedError

logger = logging.getLogger(__name__)
WINDOW_LIMIT = 15
LOCK_WAIT_SECONDS = 40.0
LOCK_RETRY_INTERVAL = 0.2
_GREETING = re.compile(
    r"^(hi|hello|hey|yo|bonjour|bonsoir|salut|coucou|good (morning|afternoon|evening))"
    r"[\s!,.]*$",
    re.IGNORECASE,
)


@dataclass
class TurnResult:
    skipped: bool
    reason: Optional[str] = None
    reply: Optional[str] = None
    conversation_id: Optional[str] = None


def _window_key_turns(turns: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for turn in turns:
        role = turn.get("role")
        content = turn.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            cleaned.append({"role": role, "content": content})
    return cleaned[-WINDOW_LIMIT:]


def _join_fragments(fragments: list[dict]) -> str:
    parts: list[str] = []
    for item in fragments:
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def _strip_buffered_user_turns(history: list[dict], fragments: list[dict]) -> list[dict]:
    """Remove trailing history rows already persisted for this debounce burst."""
    trimmed = list(history)
    for item in reversed(fragments):
        text = item.get("text")
        if (
            trimmed
            and trimmed[-1].get("role") == "user"
            and trimmed[-1].get("content") == text
        ):
            trimmed.pop()
        else:
            break
    return trimmed


async def _hold_conversation_lock(
    cache: CachePort,
    tenant_id: str,
    channel: str,
    user_channel_id: str,
    *,
    wait: bool,
) -> AsyncIterator[None]:
    """Yield once under the conversation lock.

    When wait=True (debounce winner), poll through LockBusy so arq does not
    Retry a job that already claimed inbound and would then be treated as a duplicate.
    """
    if not wait:
        async with cache.lock(tenant_id, channel, user_channel_id):
            yield
        return

    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            async with cache.lock(tenant_id, channel, user_channel_id):
                yield
            return
        except LockBusy:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(LOCK_RETRY_INTERVAL)


async def handle_turn(event: InboundMessageEvent, container: Container) -> TurnResult:
    role = "broker" if event.is_business_echo else "user"
    conversation_id = await container.conversations.claim_inbound(event, role)
    if conversation_id is None:
        return TurnResult(skipped=True, reason="duplicate")

    if await container.cache.was_sent_by_bot(event.tenant_id, event.provider_message_id):
        return TurnResult(skipped=True, reason="echo", conversation_id=conversation_id)

    if event.is_business_echo:
        await container.cache.set_human_active(
            event.tenant_id, event.channel, event.user_channel_id
        )
        logger.info(
            "HITL engaged tenant=%s user=%s msg=%s",
            event.tenant_id,
            event.user_channel_id,
            event.provider_message_id,
        )
        return TurnResult(skipped=True, reason="echo", conversation_id=conversation_id)

    if not event.text or not event.text.strip():
        return TurnResult(skipped=True, reason="no_text", conversation_id=conversation_id)

    user_text = event.text.strip()
    fragments: list[dict] = [
        {"text": user_text, "provider_message_id": event.provider_message_id}
    ]
    debounce_seconds = float(getattr(container, "debounce_seconds", 0) or 0)
    debounce_token: Optional[int] = None

    if debounce_seconds > 0:
        await container.cache.append_debounce_fragment(
            event.tenant_id,
            event.channel,
            event.user_channel_id,
            text=user_text,
            provider_message_id=event.provider_message_id,
        )
        debounce_token = await container.cache.bump_debounce_generation(
            event.tenant_id, event.channel, event.user_channel_id
        )
        await asyncio.sleep(debounce_seconds)
        current = await container.cache.get_debounce_generation(
            event.tenant_id, event.channel, event.user_channel_id
        )
        if current != debounce_token:
            logger.info(
                "debounced tenant=%s user=%s msg=%s token=%s current=%s",
                event.tenant_id,
                event.user_channel_id,
                event.provider_message_id,
                debounce_token,
                current,
            )
            return TurnResult(skipped=True, reason="debounced", conversation_id=conversation_id)

    try:
        async for _ in _hold_conversation_lock(
            container.cache,
            event.tenant_id,
            event.channel,
            event.user_channel_id,
            wait=debounce_seconds > 0,
        ):
            if debounce_token is not None:
                current = await container.cache.get_debounce_generation(
                    event.tenant_id, event.channel, event.user_channel_id
                )
                if current != debounce_token:
                    return TurnResult(
                        skipped=True, reason="debounced", conversation_id=conversation_id
                    )
                drained = await container.cache.drain_debounce_fragments(
                    event.tenant_id, event.channel, event.user_channel_id
                )
                if drained:
                    fragments = drained
                user_text = _join_fragments(fragments)
                if not user_text:
                    return TurnResult(
                        skipped=True, reason="empty_buffer", conversation_id=conversation_id
                    )

            if await container.cache.is_human_active(
                event.tenant_id, event.channel, event.user_channel_id
            ):
                logger.warning(
                    "HITL mute tenant=%s user=%s", event.tenant_id, event.user_channel_id
                )
                return TurnResult(skipped=True, reason="hitl", conversation_id=conversation_id)

            tenant = await container.tenants.get(event.tenant_id)
            if tenant is None:
                raise RuntimeError(f"Unknown tenant {event.tenant_id}")
            if not tenant.bot_should_run(datetime.now(timezone.utc)):
                logger.info(
                    "bot muted tenant=%s reason=hours_or_inactive", event.tenant_id
                )
                return TurnResult(
                    skipped=True, reason="outside_hours", conversation_id=conversation_id
                )

            window = await container.cache.get_window(
                event.tenant_id, event.channel, event.user_channel_id
            )
            if not window:
                window = await container.conversations.recent_turns(
                    event.tenant_id, event.channel, event.user_channel_id
                )
            history = _strip_buffered_user_turns(_window_key_turns(window), fragments)
            if _should_skip_repeat_greeting(history, user_text):
                logger.warning(
                    "skip repeat greeting tenant=%s user=%s",
                    event.tenant_id,
                    event.user_channel_id,
                )
                return TurnResult(
                    skipped=True, reason="repeat_greeting", conversation_id=conversation_id
                )

            result = await orchestrate(
                container=container,
                tenant=tenant,
                conversation_id=conversation_id,
                history=history,
                user_text=user_text,
                channel=event.channel,
                user_channel_id=event.user_channel_id,
            )

            try:
                outbound_id = await container.sender.send(
                    event.tenant_id, event.channel, event.user_channel_id, result.reply
                )
            except SessionWindowClosedError as exc:
                await _notify_session_window_closed(
                    container,
                    tenant=tenant,
                    channel=event.channel,
                    user_channel_id=event.user_channel_id,
                    conversation_id=conversation_id,
                    draft_reply=result.reply,
                    detail=exc.detail,
                )
                return TurnResult(
                    skipped=True,
                    reason="session_window_closed",
                    conversation_id=conversation_id,
                )
            await container.cache.remember_outbound(event.tenant_id, outbound_id)
            await container.conversations.append_assistant(
                conversation_id=conversation_id,
                tenant_id=event.tenant_id,
                text=result.reply,
                provider_message_id=outbound_id,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                latency_ms=result.latency_ms,
            )
            updated = history + [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": result.reply},
            ]
            await container.cache.replace_window(
                event.tenant_id,
                event.channel,
                event.user_channel_id,
                _window_key_turns(updated),
            )
            logger.warning(
                "dispatched tenant=%s channel=%s msg=%s fragments=%s outbound=%s latency_ms=%s tokens_in=%s tokens_out=%s",
                event.tenant_id,
                event.channel,
                event.provider_message_id,
                len(fragments),
                outbound_id,
                result.latency_ms,
                result.tokens_in,
                result.tokens_out,
            )
            return TurnResult(skipped=False, reply=result.reply, conversation_id=conversation_id)
        return TurnResult(skipped=True, reason="lock_timeout", conversation_id=conversation_id)
    except LockBusy:
        raise
    except Exception:
        logger.exception("turn failed tenant=%s msg=%s", event.tenant_id, event.provider_message_id)
        raise


async def _notify_session_window_closed(
    container: Container,
    *,
    tenant: TenantContext,
    channel: str,
    user_channel_id: str,
    conversation_id: str,
    draft_reply: str,
    detail: str,
) -> None:
    digits = "".join(ch for ch in user_channel_id if ch.isdigit())
    payload = {
        "ok": False,
        "reason": "session_window_closed",
        "code": SessionWindowClosedError.code,
        "agency_name": tenant.agency_name,
        "channel": channel,
        "guest_channel": channel,
        "guest_user_id": user_channel_id,
        "conversation_id": conversation_id,
        "draft_reply": draft_reply,
        "detail": detail,
        "tenant_telegram_chat_id": tenant.telegram_chat_id,
        "alert_brokers": [
            {
                "name": broker.name,
                "phone_number": broker.phone_number,
                "telegram_chat_id": broker.telegram_chat_id,
            }
            for broker in pick_alert_brokers(tenant)
        ],
    }
    if channel == "whatsapp" and digits:
        payload["wa_me"] = f"https://wa.me/{digits}"
        payload["mute_command"] = f"/mute_{digits}_{tenant.id}"
    logger.error(
        "session window closed tenant=%s user=%s conversation=%s",
        tenant.id,
        user_channel_id,
        conversation_id,
    )
    alerts = getattr(container, "ops_alerts", None)
    if alerts is None:
        return
    try:
        await alerts.notify_handoff(tenant.id, payload)
    except Exception:
        logger.exception(
            "failed to notify brokers of session window close tenant=%s", tenant.id
        )


def _is_greeting(text: str) -> bool:
    return bool(_GREETING.match((text or "").strip()))


def _should_skip_repeat_greeting(history: list[dict], user_text: str) -> bool:
    if not _is_greeting(user_text) or not history:
        return False
    if not any(turn.get("role") == "assistant" for turn in history):
        return False
    last_user = next(
        (turn.get("content") for turn in reversed(history) if turn.get("role") == "user"),
        None,
    )
    return last_user is None or _is_greeting(str(last_user))


def new_test_message_id() -> str:
    return f"test-{uuid4()}"
