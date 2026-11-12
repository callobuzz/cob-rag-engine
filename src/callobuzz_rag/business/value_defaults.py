"""Suggested value settings per kind of business (TV4).

The numbers are editable starting points from `data/value_defaults.json`, in minor units of the
workspace currency; the dashboard labels them "suggested". A kind of business with no row falls
back to `generic_support`.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from callobuzz_rag.business.value import ValueSettingsIn

DEFAULTS_PATH = Path(__file__).parent / "data" / "value_defaults.json"
FALLBACK_KEY = "generic_support"


@lru_cache(maxsize=1)
def _table() -> dict[str, dict[str, dict[str, int]]]:
    data: dict[str, Any] = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def known_business_types() -> list[str]:
    return sorted(_table()["INR"])


def suggested_for(business_type: str | None, currency: str) -> ValueSettingsIn:
    """The suggestion for a kind of business in `currency` (INR unless USD)."""
    rows = _table()["USD" if currency == "USD" else "INR"]
    row = rows.get(business_type or "", rows[FALLBACK_KEY])
    return ValueSettingsIn(
        lead_value_minor=row["lead"],
        handoff_value_minor=row["handoff"],
        staff_hour_minor=row["staff_hour"],
        minutes_per_answer=row["minutes"],
    )
