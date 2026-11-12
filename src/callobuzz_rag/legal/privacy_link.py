"""The deployer's published privacy notice, as an address a visitor's browser can open.

The widget's footer and the lead consent notice link to the business's own privacy policy when it
set one (the business is the data fiduciary); otherwise to the deployer's published Privacy
notice.

What is cached per process is only a fact (is a privacy page published?) and, from it, the address
built from the configured `RAG_PUBLIC_URL`. A URL derived from a request's Host header never goes
into shared state: the widget builds its footer link per request with `page_url(base)`. The
consent text reads `consent_url()` through the runtime config (`conversation/store.py` sets it),
so every surface (widget, dashboard preview, WhatsApp worker) records the same wording. A published
page is never taken back, so a stale cache can only lack the link for a few seconds; a failed read
keeps the last known value and never fails a request.
"""

import logging
import time
from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from callobuzz_rag.db.models.legal import LegalDocument

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

TTL_S: Final = 30.0

_published: bool = False
_consent_url: str | None = None
_expires: float = 0.0


def published() -> bool:
    """Whether the deployer has published a privacy notice (as last read)."""
    return _published


def consent_url() -> str | None:
    """The notice's address from the configured public URL only; None when none is published or
    `RAG_PUBLIC_URL` is not set (a consent record never carries a request-derived host)."""
    return _consent_url


def page_url(base_url: str) -> str | None:
    """The notice's address under `base_url` (built per request, never cached), or None."""
    return f"{base_url.rstrip('/')}/legal/privacy" if _published else None


def reset() -> None:
    """Forget the cached state (tests)."""
    global _published, _consent_url, _expires
    _published, _consent_url, _expires = False, None, 0.0


async def ensure(session: "AsyncSession", public_url: str | None = None) -> bool:
    """Refresh the cached fact when it is older than `TTL_S`; returns whether a page is published.

    The probe runs in a SAVEPOINT so a failed statement cannot poison the caller's transaction.
    """
    global _published, _consent_url, _expires
    now = time.monotonic()
    if now < _expires:
        return _published
    try:
        async with session.begin_nested():
            version = (
                await session.execute(
                    select(LegalDocument.version)
                    .where(LegalDocument.slug == "privacy", LegalDocument.version.is_not(None))
                    .limit(1)
                )
            ).scalar_one_or_none()
        _published = isinstance(version, int)
        if public_url is None:
            from callobuzz_rag.core.config import get_settings

            public_url = get_settings().public_url
        base = (public_url or "").rstrip("/")
        _consent_url = f"{base}/legal/privacy" if _published and base else None
    except Exception:
        logger.warning("could not read the published privacy notice", exc_info=True)
    _expires = now + TTL_S
    return _published
