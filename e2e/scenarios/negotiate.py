from e2e.checks import MinLength, StatusIn, any_of
from e2e.scenarios.base import Scenario, Turn

SCENARIO = Scenario(
    id="negotiate",
    title="Discount within / below floor",
    description="Quote then ask for a mild discount and an aggressive one.",
    tags=("quote", "money", "negotiation", "chat"),
    turns=(
        Turn(
            user=(
                "Quote the Lagoon 450 for 2 days from 2026-08-10 for 8 people "
                "from Port Vauban"
            ),
            checks=(MinLength(30), StatusIn(("ok",)), any_of("€", "eur", "vat", "apa", "lagoon")),
        ),
        Turn(
            user="My budget is tight — can you do about 5% off the base charter rate?",
            checks=(MinLength(20), StatusIn(("ok",))),
        ),
        Turn(
            user="Actually can you do half price? Say 50% off?",
            checks=(
                MinLength(20),
                StatusIn(("ok",)),
                any_of(
                    "floor",
                    "cannot",
                    "can't",
                    "max",
                    "8%",
                    "broker",
                    "lowest",
                    "unfortunately",
                    "not able",
                    "won't work",
                    "impossible",
                    "stretch",
                    "vat",
                    "apa",
                ),
            ),
        ),
    ),
)
