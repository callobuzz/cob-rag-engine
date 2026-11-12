"""The public side of website freshness (BIZ2).

`POST|GET /api/v1/public/learn/{key}`: a site tells the engine that a page changed (the learn
key is the credential; see `website/push.py`). `POST /api/v1/widget/page`: the widget on a
visitor's page reports a short code of what it shows, so a changed page is noticed while people
browse (see `website/sensor.py`). Neither takes content from outside: both only ask the server to
read a URL of the owner's own site again, and the server's own read decides.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.widget import _authorize_widget, _limiter
from callobuzz_rag.api.schemas.website_public import (
    LearnAcceptedOut,
    LearnPushRequest,
    WidgetPageRequest,
)
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.website import push, sensor, service
from callobuzz_rag.widget.security import request_client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public/learn", tags=["website"])
widget_router = APIRouter(prefix="/api/v1/widget", tags=["widget"])  # BIZ2 sensor


async def _push(
    request: Request, session: AsyncSession, key: str, urls: list[str], body_key: str | None = None
) -> LearnAcceptedOut:
    settings = request.app.state.settings
    redis = push.get_redis(request.app)
    await push.charge_ip(redis, request_client_ip(request))
    row = await push.resolve_key(session, key)
    if row is None:
        raise APIError(404, push.INVALID_KEY, "learn_key_invalid")
    # An IndexNow client sends its own key in the body; only one of ours can mismatch.
    if body_key is not None and body_key.startswith(push.KEY_PREFIX) and body_key != key:
        raise APIError(
            422, "The key in the request is not the key in the address.", "learn_key_mismatch"
        )
    corpus = await session.get(Corpus, row.corpus_id)
    tenant = await session.get(Tenant, corpus.tenant_id) if corpus is not None else None
    if corpus is None or tenant is None or tenant.status != "active":
        raise APIError(404, push.INVALID_KEY, "learn_key_invalid")  # same as an unknown key
    service.refuse_disabled(await service.policy_of(session, corpus, settings))
    urls = urls[: push.MAX_URLS]
    await push.charge(redis, row.learn_key_hash or "", max(1, len(urls)), settings)
    accepted = await push.accept_urls(session, row, urls)
    rejected = accepted.rejected
    for source_id, source_urls in accepted.by_source.items():
        fresh = await push.once_per_minute(redis, source_id, source_urls)
        if fresh and not push.dispatch_urls(source_id, fresh, "push", "push"):
            await push.release_urls(redis, source_id, fresh)  # so the retry is queued
            raise APIError(503, push.UNAVAILABLE, "learn_unavailable")
    return LearnAcceptedOut(accepted=accepted.accepted, rejected=rejected)


@router.post("/{key}", response_model=LearnAcceptedOut, status_code=202)
async def push_urls(
    key: str,
    body: LearnPushRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LearnAcceptedOut:
    """Tell the engine that pages of your website changed, so they are read again within a
    minute. The key in the address is made in the dashboard (Knowledge, From your website). Send
    `url` or `urlList` (up to 100); only pages on this knowledge base's own sites are read.
    Limited per key; 429 `learn_rate_limited` with `Retry-After`. No other credential."""
    return await _push(request, session, key, body.urls(), body.key)


@router.get("/{key}", response_model=LearnAcceptedOut, status_code=202)
async def push_url(
    key: str,
    request: Request,
    url: Annotated[str, Query(min_length=1, max_length=2048)],
    session: AsyncSession = Depends(get_session),
) -> LearnAcceptedOut:
    """The same as the POST form, for one page, from a link or a webhook that can only GET."""
    return await _push(request, session, key, [url])


# BIZ2 sensor
@widget_router.post("/page", status_code=204)
async def widget_page(
    body: WidgetPageRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_widget_key: str | None = Header(None),
) -> Response:
    """The widget reports the page it is on: its address and a short code of its visible text
    (never the text). A changed code makes the engine read that page again from its own
    server; nothing is ever learned from the browser. Always 204: the page is told nothing
    about learning. Same credential as the rest of the widget (`X-Widget-Key` + Origin)."""
    key, corpus = await _authorize_widget(request, session, x_widget_key)
    try:
        limit = await _limiter(request).check_event(
            str(key.id), request_client_ip(request), key.rate_limit_per_minute
        )
        if limit.allowed:
            await sensor.report(
                session,
                push.get_redis(request.app),
                request.app.state.settings,
                corpus,
                origin=request.headers.get("origin"),
                page_url=body.page_url,
                fingerprint=body.fingerprint,
                now=datetime.now(UTC),
            )
    except Exception:  # a sensor report must never fail a visitor's page
        logger.warning("Widget page report failed", exc_info=True)
    return Response(status_code=204)


# /BIZ2 sensor
