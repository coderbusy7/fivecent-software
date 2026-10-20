from __future__ import annotations

from typing import TYPE_CHECKING

from fivecent.application.turn import handle_turn
from fivecent.domain.events import InboundMessageEvent

if TYPE_CHECKING:
    from fivecent.application.ports import Container


class InlineQueue:
    """Dev/test queue: run the turn on the caller (no Redis)."""

    def __init__(self) -> None:
        self.container: Container | None = None

    async def enqueue(self, event: InboundMessageEvent) -> None:
        if self.container is None:
            raise RuntimeError("InlineQueue is not bound to a container.")
        await handle_turn(event, self.container)
