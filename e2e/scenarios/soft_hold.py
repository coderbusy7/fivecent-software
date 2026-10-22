from e2e.checks import MinLength, StatusIn, any_of
from e2e.scenarios.base import Scenario, Turn

SCENARIO = Scenario(
    id="soft_hold",
    title="24h soft hold",
    description="Quote context then request a soft hold with name + email.",
    tags=("hold", "chat"),
    turns=(
        Turn(
            user=(
                "Quote the Lagoon 450 for 2 days from 2026-09-12 for 8 people "
                "from Port Vauban"
            ),
            checks=(MinLength(20), StatusIn(("ok",))),
        ),
        Turn(
            user=(
                "Please place a 24h soft hold on the Lagoon 450 for 2026-09-12. "
                "Name: Alex Test, email: alex.e2e@example.com"
            ),
            checks=(
                MinLength(20),
                StatusIn(("ok",)),
                any_of(
                    "hold",
                    "option",
                    "24",
                    "email",
                    "broker",
                    "reserved",
                    "reserve",
                    "confirm",
                    "noted",
                ),
            ),
        ),
    ),
)
