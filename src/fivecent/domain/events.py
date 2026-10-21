from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

Channel = str


class InboundMessageEvent(BaseModel):
    tenant_id: str
    channel: Channel
    user_channel_id: str
    provider_message_id: str
    text: Optional[str] = None
    attachments: list[str] = Field(default_factory=list)
    timestamp: datetime
    is_business_echo: bool = False


class OutboundMessage(BaseModel):
    tenant_id: str
    channel: Channel
    user_channel_id: str
    text: str
