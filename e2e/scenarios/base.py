from __future__ import annotations

from dataclasses import dataclass, field

from e2e.checks import Check


@dataclass(frozen=True)
class Turn:
    user: str
    checks: tuple[Check, ...] = ()
    pause_seconds: float | None = None
    # webhook mode cannot assert reply text; chat mode can.
    require_reply: bool = True


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    description: str
    turns: tuple[Turn, ...]
    tags: tuple[str, ...] = field(default_factory=tuple)
