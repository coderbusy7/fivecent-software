from e2e.checks import MinLength, StatusIn, any_of, none_of
from e2e.scenarios.base import Scenario, Turn

SCENARIO = Scenario(
    id="pilot_suite",
    title="Full pilot conversation suite",
    description=(
        "Multi-turn path: greeting → quote → mild discount → floor → other port → "
        "handoff → soft hold. Best with mode=chat and a unique E2E_PHONE per run."
    ),
    tags=("suite", "pilot", "chat"),
    turns=(
        Turn(
            user="Hi Elena — looking for a day charter this summer",
            checks=(MinLength(15), StatusIn(("ok",))),
        ),
        Turn(
            user=(
                "Quote the Lagoon 450 for 2 days from 2026-07-18 for 8 people, "
                "leaving from Port Vauban"
            ),
            checks=(
                MinLength(30),
                StatusIn(("ok",)),
                any_of("€", "eur", "vat", "apa", "lagoon", "total", "quote"),
            ),
        ),
        Turn(
            user="My budget is a bit tight — can you do about 5% off the base charter rate?",
            checks=(MinLength(15), StatusIn(("ok",))),
        ),
        Turn(
            user="Actually can you do half price? Say 50% off?",
            checks=(MinLength(15), StatusIn(("ok",))),
        ),
        Turn(
            user=(
                "We might prefer to depart from Monaco instead of Antibes — "
                "can you arrange that?"
            ),
            checks=(
                MinLength(15),
                StatusIn(("ok",)),
                none_of("out of scope", "cannot help"),
            ),
        ),
        Turn(
            user=(
                "Yes please — have a broker WhatsApp me later about Monaco "
                "repositioning. My name is Alex Test."
            ),
            checks=(MinLength(15), StatusIn(("ok",))),
        ),
        Turn(
            user=(
                "Please place a 24h soft hold on the Lagoon 450 for 2026-07-18. "
                "Name: Alex Test, email: alex.e2e@example.com"
            ),
            checks=(
                MinLength(15),
                StatusIn(("ok",)),
                any_of("hold", "option", "24", "broker", "email", "confirm", "noted"),
            ),
        ),
    ),
)
