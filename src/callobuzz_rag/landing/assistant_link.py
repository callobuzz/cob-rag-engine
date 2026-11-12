"""Connect the website's "Ask it yourself" button to a real assistant (LP4).

The button loads the deployer's real widget on click, which needs a widget key whose allowed
origins include the website's address. Connecting reuses an active key of the chosen operator
knowledge base that already allows the address; otherwise it asks the widget-key route for a new
key named "Public website" with that one origin, using the caller's own credential so the
route's rules, plan caps and audit trail run. Other keys are never edited. Disconnecting only
clears the link.
"""

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.landing import store
from callobuzz_rag.onboarding.service import _raise_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.landing import LandingSite

KEY_NAME: Final = "Public website"


@dataclass(frozen=True)
class Connected:
    site: "LandingSite"
    key_id: uuid.UUID
    created: bool


def _normal(origin: str) -> str:
    return str(origin).rstrip("/").lower()


async def _operator_kb(session: "AsyncSession", corpus_id: uuid.UUID) -> Corpus:
    from callobuzz_rag.accounts.operator import get_operator_tenant

    corpus = await session.get(Corpus, corpus_id)
    operator = await get_operator_tenant(session)
    if corpus is None or operator is None or corpus.tenant_id != operator.id:
        raise APIError(
            409,
            "Choose one of your own knowledge bases (not a customer's).",
            "landing_not_operator_kb",
        )
    return corpus


async def connect(
    session: "AsyncSession",
    *,
    client: Any,
    corpus_id: uuid.UUID,
    origin: str | None,
    updated_by: str,
) -> Connected:
    """Point "Ask it yourself" at a key of this operator knowledge base."""
    if origin is None:
        raise APIError(
            409,
            "Set your website's address (or RAG_PUBLIC_URL) before switching the site on.",
            "landing_needs_address",
        )
    await _operator_kb(session, corpus_id)
    site = await store.ensure_site(session)
    wanted = _normal(origin)
    rows = (
        (await session.execute(select(WidgetKey).where(WidgetKey.corpus_id == corpus_id)))
        .scalars()
        .all()
    )
    key_id: uuid.UUID | None = None
    for key in rows:
        if key.is_active and wanted in {_normal(o) for o in (key.allowed_origins or [])}:
            key_id = key.id
            break
    created = key_id is None
    if key_id is None:
        response = await client.request(
            "POST",
            f"/api/v1/corpora/{corpus_id}/widget-keys",
            json={"name": KEY_NAME, "allowed_origins": [origin.rstrip("/")]},
        )
        if not response.ok:
            _raise_for(response)
        raw = response.body.get("id") if isinstance(response.body, dict) else None
        try:
            key_id = uuid.UUID(str(raw))
        except ValueError as exc:
            raise APIError(
                502, "The assistant could not be connected.", "landing_assistant_failed"
            ) from exc
        await session.flush()
    site.ask_corpus_id = corpus_id
    site.ask_widget_key_id = key_id
    site.updated_by = updated_by[:320]
    await session.flush()
    return Connected(site=site, key_id=key_id, created=created)


async def disconnect(session: "AsyncSession", *, updated_by: str) -> "LandingSite":
    """Stop using the key. The key itself is not changed."""
    site = await store.ensure_site(session)
    site.ask_corpus_id = None
    site.ask_widget_key_id = None
    site.updated_by = updated_by[:320]
    await session.flush()
    return site
