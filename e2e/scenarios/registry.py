from __future__ import annotations

from e2e.scenarios.base import Scenario
from e2e.scenarios.greeting import SCENARIO as greeting
from e2e.scenarios.handoff import SCENARIO as handoff
from e2e.scenarios.negotiate import SCENARIO as negotiate
from e2e.scenarios.other_port import SCENARIO as other_port
from e2e.scenarios.pilot_suite import SCENARIO as pilot_suite
from e2e.scenarios.quote import SCENARIO as quote
from e2e.scenarios.soft_hold import SCENARIO as soft_hold
from e2e.scenarios.webhook_smoke import SCENARIO as webhook_smoke

SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in (
        greeting,
        quote,
        negotiate,
        other_port,
        handoff,
        soft_hold,
        webhook_smoke,
        pilot_suite,
    )
}


def list_scenarios() -> list[Scenario]:
    return list(SCENARIOS.values())


def get_scenario(scenario_id: str) -> Scenario:
    try:
        return SCENARIOS[scenario_id]
    except KeyError as exc:
        known = ", ".join(sorted(SCENARIOS))
        raise KeyError(f"Unknown scenario {scenario_id!r}. Known: {known}") from exc
