from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    name: str
    detail: str = ""


class Check(Protocol):
    name: str

    def evaluate(self, reply: str, *, status: str | None = None) -> CheckResult: ...


@dataclass(frozen=True)
class ContainsAny:
    needles: tuple[str, ...]
    name: str = "contains_any"
    case_insensitive: bool = True

    def evaluate(self, reply: str, *, status: str | None = None) -> CheckResult:
        hay = reply.lower() if self.case_insensitive else reply
        found = [
            n for n in self.needles if (n.lower() if self.case_insensitive else n) in hay
        ]
        ok = bool(found)
        return CheckResult(
            ok=ok,
            name=self.name,
            detail=f"matched={found!r} needles={list(self.needles)!r}",
        )


@dataclass(frozen=True)
class ContainsAll:
    needles: tuple[str, ...]
    name: str = "contains_all"
    case_insensitive: bool = True

    def evaluate(self, reply: str, *, status: str | None = None) -> CheckResult:
        hay = reply.lower() if self.case_insensitive else reply
        missing = [
            n
            for n in self.needles
            if (n.lower() if self.case_insensitive else n) not in hay
        ]
        return CheckResult(
            ok=not missing,
            name=self.name,
            detail=f"missing={missing!r}",
        )


@dataclass(frozen=True)
class NotContains:
    needles: tuple[str, ...]
    name: str = "not_contains"
    case_insensitive: bool = True

    def evaluate(self, reply: str, *, status: str | None = None) -> CheckResult:
        hay = reply.lower() if self.case_insensitive else reply
        hit = [
            n for n in self.needles if (n.lower() if self.case_insensitive else n) in hay
        ]
        return CheckResult(ok=not hit, name=self.name, detail=f"unexpected={hit!r}")


@dataclass(frozen=True)
class MinLength:
    minimum: int
    name: str = "min_length"

    def evaluate(self, reply: str, *, status: str | None = None) -> CheckResult:
        ok = len(reply.strip()) >= self.minimum
        return CheckResult(
            ok=ok, name=self.name, detail=f"len={len(reply.strip())} min={self.minimum}"
        )


@dataclass(frozen=True)
class Regex:
    pattern: str
    name: str = "regex"
    flags: int = re.IGNORECASE

    def evaluate(self, reply: str, *, status: str | None = None) -> CheckResult:
        ok = re.search(self.pattern, reply, self.flags) is not None
        return CheckResult(ok=ok, name=self.name, detail=f"pattern={self.pattern!r}")


@dataclass(frozen=True)
class StatusIn:
    allowed: tuple[str, ...]
    name: str = "status_in"

    def evaluate(self, reply: str, *, status: str | None = None) -> CheckResult:
        ok = status in self.allowed
        return CheckResult(
            ok=ok, name=self.name, detail=f"status={status!r} allowed={list(self.allowed)!r}"
        )


def any_of(*needles: str) -> ContainsAny:
    return ContainsAny(tuple(needles))


def all_of(*needles: str) -> ContainsAll:
    return ContainsAll(tuple(needles))


def none_of(*needles: str) -> NotContains:
    return NotContains(tuple(needles))
