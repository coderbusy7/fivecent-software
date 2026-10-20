from datetime import datetime, timezone

from pydantic import BaseModel, Field

from fivecent.application.turn import new_test_message_id
from fivecent.domain.events import InboundMessageEvent


class ChatInput(BaseModel):
    phone: str = Field(min_length=1, pattern=r"\S")
    message: str = Field(min_length=1, pattern=r"\S")


def chat_to_event(payload: ChatInput, tenant_id: str) -> InboundMessageEvent:
    return InboundMessageEvent(
        tenant_id=tenant_id,
        channel="test",
        user_channel_id=payload.phone,
        provider_message_id=new_test_message_id(),
        text=payload.message,
        timestamp=datetime.now(timezone.utc),
    )
