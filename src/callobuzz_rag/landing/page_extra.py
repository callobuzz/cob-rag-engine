"""What a page takes from the visitor's request besides its path (LP3).

The pricing page shows the currency and billing period the link names (`?currency=USD&period=
yearly`, a plain link that works without JavaScript); any other value shows the default. The
canonical address never carries the query. Nothing here reads the `Host` header.
"""

from collections.abc import Mapping
from typing import Any


def for_page(page: str, query: Mapping[str, str]) -> dict[str, Any]:
    """The extra render values a page needs from its query string."""
    if page == "pricing":
        return {"currency": query.get("currency"), "period": query.get("period")}
    return {}
