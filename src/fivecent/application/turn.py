from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fivecent.application.orchestrator import orchestrate
from fivecent.application.ports import Container, LockBusy
from fivecent.domain.events import InboundMessageEvent

logger = logging.getLogger(__name__)
WINDOW_LIMIT = 15
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

    try:
        async with container.cache.lock(event.tenant_id, event.channel, event.user_channel_id):
            if await container.cache.is_human_active(
                event.tenant_id, event.channel, event.user_channel_id
            ):
                logger.info(
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
            history = _window_key_turns(window)
            if (
                history
                and history[-1].get("role") == "user"
                and history[-1].get("content") == event.text
            ):
                history = history[:-1]
            if _should_skip_repeat_greeting(history, event.text):
                logger.info(
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
                user_text=event.text,
                channel=event.channel,
                user_channel_id=event.user_channel_id,
            )

            outbound_id = await container.sender.send(
                event.tenant_id, event.channel, event.user_channel_id, result.reply
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
                {"role": "user", "content": event.text},
                {"role": "assistant", "content": result.reply},
            ]
            await container.cache.replace_window(
                event.tenant_id,
                event.channel,
                event.user_channel_id,
                _window_key_turns(updated),
            )
            logger.info(
                "dispatched tenant=%s channel=%s msg=%s outbound=%s latency_ms=%s tokens_in=%s tokens_out=%s",
                event.tenant_id,
                event.channel,
                event.provider_message_id,
                outbound_id,
                result.latency_ms,
                result.tokens_in,
                result.tokens_out,
            )
            return TurnResult(skipped=False, reply=result.reply, conversation_id=conversation_id)
    except LockBusy:
        raise
    except Exception:
        logger.exception("turn failed tenant=%s msg=%s", event.tenant_id, event.provider_message_id)
        raise


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
