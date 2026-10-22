from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")


@dataclass(frozen=True)
class E2EConfig:
    api_base: str
    phone: str
    whatsapp_app_secret: str
    whatsapp_phone_number_id: str
    pause_seconds: float
    mode: str  # chat | webhook

    @classmethod
    def from_env(cls, *, mode: str | None = None) -> E2EConfig:
        _load_env()
        phone = os.environ.get("E2E_PHONE", "33621611871")
        digits = "".join(ch for ch in phone if ch.isdigit())
        if digits.startswith("00"):
            digits = digits[2:]
        return cls(
            api_base=os.environ.get("E2E_API_BASE")
            or os.environ.get("API_BASE")
            or os.environ.get("ADMIN_API_BASE")
            or "https://fivecent-software.onrender.com",
            phone=digits,
            whatsapp_app_secret=os.environ.get("WHATSAPP_APP_SECRET", ""),
            whatsapp_phone_number_id=os.environ.get(
                "WHATSAPP_PHONE_NUMBER_ID", "1350102171528381"
            ),
            pause_seconds=float(os.environ.get("E2E_PAUSE_SECONDS", "1.5")),
            mode=(mode or os.environ.get("E2E_MODE", "chat")).strip().lower(),
        )
