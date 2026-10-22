from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from e2e.checks import CheckResult
from e2e.client import TurnExchange, build_client
from e2e.config import E2EConfig
from e2e.scenarios.base import Scenario, Turn


@dataclass
class TurnReport:
    index: int
    user: str
    reply: str
    status: str
    mode: str
    elapsed_ms: int
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks) if self.checks else self.status in {
            "ok",
            "skipped",
            "debounced",
            "repeat_greeting",
        }


@dataclass
class ScenarioReport:
    scenario_id: str
    title: str
    mode: str
    phone: str
    api_base: str
    started_at: str
    finished_at: str = ""
    turns: list[TurnReport] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.turns) and all(t.ok for t in self.turns)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "mode": self.mode,
            "phone": self.phone,
            "api_base": self.api_base,
            "ok": self.ok,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "turns": [
                {
                    "index": t.index,
                    "ok": t.ok,
                    "user": t.user,
                    "reply": t.reply,
                    "status": t.status,
                    "mode": t.mode,
                    "elapsed_ms": t.elapsed_ms,
                    "checks": [
                        {"ok": c.ok, "name": c.name, "detail": c.detail} for c in t.checks
                    ],
                }
                for t in self.turns
            ],
        }


def _evaluate_turn(
    turn: Turn, exchange: TurnExchange, *, mode: str
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for check in turn.checks:
        # Reply-text checks are only meaningful in chat mode.
        if mode == "webhook" and check.name not in {"status_in"}:
            continue
        if mode == "webhook" and turn.require_reply is False and check.name == "min_length":
            continue
        results.append(check.evaluate(exchange.reply, status=exchange.status))
    if mode == "chat" and turn.require_reply and turn.checks:
        # Ensure we actually got model text when checks expect content.
        needs_text = any(c.name != "status_in" for c in turn.checks)
        if needs_text and not exchange.reply.strip():
            results.append(
                CheckResult(
                    ok=False,
                    name="non_empty_reply",
                    detail=f"status={exchange.status!r}",
                )
            )
    return results


def run_scenario(scenario: Scenario, config: E2EConfig) -> ScenarioReport:
    report = ScenarioReport(
        scenario_id=scenario.id,
        title=scenario.title,
        mode=config.mode,
        phone=config.phone,
        api_base=config.api_base,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    client = build_client(config)
    try:
        for index, turn in enumerate(scenario.turns, start=1):
            pause = (
                turn.pause_seconds
                if turn.pause_seconds is not None
                else config.pause_seconds
            )
            if index > 1 and pause > 0:
                time.sleep(pause)
            exchange = client.send(turn.user)
            checks = _evaluate_turn(turn, exchange, mode=config.mode)
            report.turns.append(
                TurnReport(
                    index=index,
                    user=turn.user,
                    reply=exchange.reply,
                    status=exchange.status,
                    mode=exchange.mode,
                    elapsed_ms=exchange.elapsed_ms,
                    checks=checks,
                )
            )
            if not all(c.ok for c in checks):
                break
    except Exception as exc:  # noqa: BLE001 — surface to report
        report.error = f"{type(exc).__name__}: {exc}"
    finally:
        client.close()
        report.finished_at = datetime.now(timezone.utc).isoformat()
    return report
