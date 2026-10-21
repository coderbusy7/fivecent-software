from __future__ import annotations

import re
from typing import Optional

from fivecent.application.ports import CachePort
from fivecent.domain.tenancy import TenantContext, pick_alert_brokers
from fivecent.services.plugin_yachting.ports import BrokerAlertPort

OFF_TOPIC_LIMIT = 3
SCOPE_MARKER = "I only handle yacht charters"

_YACHTING = re.compile(
    r"\b("
    r"yacht|yachts|yachtin[g]?|boat|boats|charter|charters|catamaran|sail|sailing|"
    r"skipper|captain|crew|tender|flybridge|marina|berth|mooring|fleet|"
    r"quote|quotes|devis|apa|vat|hold|option|booking|book|reserve|"
    r"availability|available|pax|passenger|passengers|guest|guests|"
    r"lagoon|sunseeker|princess|azimut|pershing|ferretti|riva|"
    r"bateau|bateaux|location|journ[eé]e|passagers"
    r")\b",
    re.IGNORECASE,
)
_GREETING = re.compile(
    r"^(hi|hello|hey|yo|bonjour|bonsoir|salut|coucou|good (morning|afternoon|evening))"
    r"[\s!,.]*$",
    re.IGNORECASE,
)
_ACK = re.compile(
    r"^(yes|yeah|yep|ok|okay|sure|please|thanks|thank you|no|nope|"
    r"oui|non|d'accord|merci)[\s!,.]*$",
    re.IGNORECASE,
)
_CONTACT = re.compile(r"[\w.+-]+@[\w.-]+\.\w+|\+\d{8,}|\d{4}-\d{2}-\d{2}")


def is_yachting_scope(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    if _GREETING.match(stripped) or _ACK.match(stripped) or _CONTACT.search(stripped):
        return True
    return bool(_YACHTING.search(stripped))


def consecutive_off_topic(history: list[dict], user_text: str) -> int:
    if is_yachting_scope(user_text):
        return 0
    count = 1
    for turn in reversed(history):
        if turn.get("role") != "user":
            continue
        content = turn.get("content")
        if not isinstance(content, str) or is_yachting_scope(content):
            break
        count += 1
    return count


def redirect_reply(tenant: TenantContext, strikes: int) -> str:
    if strikes < OFF_TOPIC_LIMIT:
        extra = (
            " What dates and how many guests?"
            if strikes == 1
            else " One more off-topic message and I will connect you with a broker. Dates and guest count?"
        )
        return f"{SCOPE_MARKER} at {tenant.home_port} — fleet, availability, quotes, and holds.{extra}"
    broker = tenant.primary_broker
    if broker and broker.phone_number:
        return (
            f"{SCOPE_MARKER}. I'm handing you to {broker.name} on {broker.phone_number}. "
            "Please call or WhatsApp them — they will take it from here."
        )
    return (
        f"{SCOPE_MARKER}. I'm handing you to the broker desk. "
        "A broker will take it from here."
    )


class YachtingGuardrails:
    def __init__(self, cache: CachePort, alerts: BrokerAlertPort) -> None:
        self._cache = cache
        self._alerts = alerts

    async def evaluate(
        self,
        *,
        tenant: TenantContext,
        history: list[dict],
        user_text: str,
        conversation_id: str,
        channel: str,
        user_channel_id: str,
    ) -> Optional[str]:
        strikes = consecutive_off_topic(history, user_text)
        if strikes == 0:
            return None
        reply = redirect_reply(tenant, strikes)
        if strikes >= OFF_TOPIC_LIMIT:
            rr = (
                await self._cache.incr(f"rr:{tenant.id}")
                if tenant.routing_mode == "round_robin"
                else 0
            )
            selected = pick_alert_brokers(tenant, rr)
            await self._alerts.notify_handoff(
                tenant.id,
                {
                    "reason": "off_topic",
                    "strikes": strikes,
                    "conversation_id": conversation_id,
                    "channel": channel,
                    "user_channel_id": user_channel_id,
                    "last_text": user_text,
                    "alert_brokers": [
                        {
                            "name": broker.name,
                            "phone_number": broker.phone_number,
                            "telegram_chat_id": broker.telegram_chat_id,
                        }
                        for broker in selected
                    ],
                },
            )
            await self._cache.set_human_active(tenant.id, channel, user_channel_id)
        return reply
