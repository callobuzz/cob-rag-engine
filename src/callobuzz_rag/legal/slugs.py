"""The eight page slugs, in the order the dashboard lists them. Kept apart from `store.py` so the
database model can name them without importing the store (which imports the model)."""

from typing import Final, Literal

Slug = Literal[
    "terms",
    "privacy",
    "refunds",
    "delivery",
    "acceptable-use",
    "dpa",
    "sub-processors",
    "grievance",
]
SLUGS: Final[tuple[Slug, ...]] = (
    "terms",
    "privacy",
    "refunds",
    "delivery",
    "acceptable-use",
    "dpa",
    "sub-processors",
    "grievance",
)
# Sign-up asks for acceptance of these two, and only when both are published.
REQUIRED_AT_SIGNUP: Final[tuple[Slug, ...]] = ("terms", "privacy")
