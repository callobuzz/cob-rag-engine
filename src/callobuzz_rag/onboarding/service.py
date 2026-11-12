"""Onboarding: set a business up from its website address, and remember where the widget loads.

Two halves. The widget half (`record_sighting`, `note_widget_load`) is best-effort: a database or
queue problem is logged and swallowed, because a visitor's chat must never break over
bookkeeping. The wizard half (`start_website`, `update_state`, `get_state`) drives the
engine's own routes through the in-process client with the caller's own credential (D14), so
owner rules, plan caps, audit, strict bodies and the website-learning trigger all run exactly as
they do on the dashboard; nothing here writes a corpus, assistant or key row itself.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

from sqlalchemy import case, delete, func, literal_column, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.onboarding import (
    ConnectionOut,
    FilesOut,  # TV5 brochure
    LearningProgressOut,
    OnboardingOut,
    RefusedOrigin,
    StartOnboardingRequest,
    UpdateOnboardingRequest,
)
from callobuzz_rag.db.models.onboarding import OnboardingState, WidgetOriginSeen
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.website import WebsiteSource
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.website import service as website_service
from callobuzz_rag.website.netguard import BlockedURLError, check_url
from callobuzz_rag.website.urls import UrlError, origin_of, site_scope
from callobuzz_rag.widget.security import normalize_origin

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

MAX_REFUSED_ORIGINS: Final = 20
REWRITE_AFTER: Final = "1 hour"
ORIGIN_MAX: Final = 255  # the column width of `widget_origin_seen.origin`
NAME_MAX: Final = 40
DEFAULT_TEMPLATE: Final = "generic_support"
WEBSITE_HELP: Final = "Enter your website like shreedental.in"
STEP_FAILED: Final = (
    "We could not finish setting up your assistant. Nothing was lost; please try again."
)


# ------------------------------------------------------------------ where the widget loads


def sighting_statement(key_id: uuid.UUID, corpus_id: uuid.UUID, origin: str, allowed: bool) -> Any:
    """Insert the sighting, or touch it at most once an hour, or flip it when `allowed` changed.

    `inserted` is true for a brand-new row (`xmax = 0`) and for a refused row that has just become
    allowed (its `first_seen_at` is reset to now, so it equals `last_seen_at`): both are "the first
    allowed load" to the caller. The flip is not held back by the hour: an owner who allows the
    `www` spelling sees the light change on the next reload.
    """
    insert = pg_insert(WidgetOriginSeen).values(
        widget_key_id=key_id, origin=origin, corpus_id=corpus_id, allowed=allowed
    )
    now = func.now()
    first_seen = (
        case((WidgetOriginSeen.allowed.is_(False), now), else_=WidgetOriginSeen.first_seen_at)
        if allowed
        else WidgetOriginSeen.first_seen_at
    )
    return insert.on_conflict_do_update(
        index_elements=[WidgetOriginSeen.widget_key_id, WidgetOriginSeen.origin],
        set_={"last_seen_at": now, "allowed": allowed, "first_seen_at": first_seen},
        where=or_(
            WidgetOriginSeen.last_seen_at < now - text(f"interval '{REWRITE_AFTER}'"),
            WidgetOriginSeen.allowed.is_distinct_from(allowed),
        ),
    ).returning(
        literal_column("(xmax = 0 OR (allowed AND first_seen_at = last_seen_at))").label("inserted")
    )


async def record_sighting(
    session: "AsyncSession",
    *,
    key_id: uuid.UUID,
    corpus_id: uuid.UUID,
    origin: str,
    allowed: bool,
) -> bool:
    """Remember that `key_id` loaded from `origin`. True when this is the first time.

    Refused origins are kept too (at most 20 per key, the oldest is replaced) so the wizard can
    say "we see your assistant on https://www.shreedental.in but only the other address is
    allowed". Commits.
    """
    if not allowed:
        refused = list(
            (
                await session.execute(
                    select(WidgetOriginSeen)
                    .where(
                        WidgetOriginSeen.widget_key_id == key_id,
                        WidgetOriginSeen.allowed.is_(False),
                    )
                    .order_by(WidgetOriginSeen.last_seen_at)
                )
            )
            .scalars()
            .all()
        )
        if origin not in {r.origin for r in refused} and len(refused) >= MAX_REFUSED_ORIGINS:
            drop = [r.origin for r in refused[: len(refused) - MAX_REFUSED_ORIGINS + 1]]
            await session.execute(
                delete(WidgetOriginSeen).where(
                    WidgetOriginSeen.widget_key_id == key_id, WidgetOriginSeen.origin.in_(drop)
                )
            )
    row = (await session.execute(sighting_statement(key_id, corpus_id, origin, allowed))).first()
    await session.commit()
    return bool(row is not None and row.inserted is True)


def _literal_origins(key: "WidgetKey") -> set[str]:
    """The origins the key names itself (never what `*` covers), as the widget compares them."""
    out: set[str] = set()
    for origin in key.allowed_origins or []:
        if origin == "*":
            continue
        try:
            out.add(normalize_origin(origin))
        except ValueError:
            continue
    return out


def _literally_allowed(origin: str, key: "WidgetKey") -> bool:
    """The origin is named in the key's list (never covered by `*`)."""
    try:
        return normalize_origin(origin) in _literal_origins(key)
    except ValueError:
        return False


def _site_host(origin: str) -> str:
    return (urlsplit(origin).hostname or "").removeprefix("www.")


def _is_twin(origin: str, allowed_origins: set[str]) -> bool:
    """The origin is another spelling (www or bare, other scheme or port) of a site the key
    names, and is not itself named. Only such a refused origin is ever remembered or offered
    for one-click allowing: the Origin header is the caller's to write, so an unrelated site is
    never put in front of the owner."""
    if origin in allowed_origins:
        return False
    host = _site_host(origin)
    return bool(host) and any(_site_host(a) == host for a in allowed_origins)


async def note_widget_load(
    session_factory: Any,
    *,
    key: "WidgetKey",
    corpus: "Corpus | None",
    origin: str | None,
    allowed: bool,
    settings: "Settings",
) -> None:
    """Called by `GET /widget/config` only. Never raises, never touches the request's session.

    The bookkeeping runs in a session of its own: a failure here rolls back only that session, so
    the config the visitor is waiting for is never disturbed. Only an origin the key names
    literally is recorded as allowed (a `*` match is not evidence of the business's site), and a
    refused origin only when it is a twin of one the key names. The first allowed sighting of a
    site that has no source yet starts learning (trigger `widget`, system actor); learning never
    starts from the Origin header alone.
    """
    if not origin or len(origin) > ORIGIN_MAX or session_factory is None:
        return
    try:
        origin = normalize_origin(origin)
    except ValueError:
        return
    literal = _literal_origins(key)
    if allowed and origin not in literal:
        return  # let in by `*` only
    if not allowed and not _is_twin(origin, literal):
        return
    try:
        async with session_factory() as own:
            try:
                first = await record_sighting(
                    own, key_id=key.id, corpus_id=key.corpus_id, origin=origin, allowed=allowed
                )
            except Exception:
                logger.warning("widget sighting not recorded", exc_info=True)
                await _quiet_rollback(own)
                return
            if corpus is None or not (first and allowed):
                return
            try:
                await _start_learning_once(own, corpus.id, origin, settings)
            except Exception:
                logger.info("learning not started from the first widget load", exc_info=True)
                await _quiet_rollback(own)
    except Exception:
        logger.warning("widget sighting bookkeeping failed", exc_info=True)


async def _quiet_rollback(session: "AsyncSession") -> None:
    try:
        await session.rollback()
    except Exception:
        logger.debug("rollback after a failed sighting also failed", exc_info=True)


async def _start_learning_once(
    session: "AsyncSession", corpus_id: uuid.UUID, origin: str, settings: "Settings"
) -> None:
    from callobuzz_rag.db.models.corpus import Corpus

    try:
        scope = site_scope(origin)
    except UrlError:
        return
    corpus = await session.get(Corpus, corpus_id)
    if corpus is None:
        return
    if any(s.scope == scope for s in await website_service.sources_of(session, corpus.id)):
        return
    await website_service.start_learning(
        session, corpus, origin, settings=settings, request=None, trigger="widget"
    )


# ------------------------------------------------------------------ the wizard's state


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_website(raw: str) -> str:
    """`shreedental.in`, `www.shreedental.in` or `https://shreedental.in/any/path` become the
    origin `https://shreedental.in` (http only when typed, the www spelling kept as typed).

    Raises:
        APIError: 422 `website_url_invalid` for anything that is not a website address.
    """
    try:
        origin = origin_of(raw)
        site_scope(origin)
    except UrlError as exc:
        raise APIError(422, WEBSITE_HELP, "website_url_invalid") from exc
    return origin


def allowed_origins_for(origin: str) -> list[str]:
    """The site and its `www` / bare twin: owners type one and their site answers on both."""
    parts = urlsplit(origin)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    twin = host.removeprefix("www.") if host.startswith("www.") else f"www.{host}"
    return [origin, f"{parts.scheme}://{twin}{port}"]


def default_assistant_name(business_name: str) -> str:
    """ "<business> Assistant", at most 40 characters, the business cut on a word."""
    suffix = " Assistant"
    name = " ".join(business_name.split())
    if not name:
        return "Assistant"
    if len(name) + len(suffix) <= NAME_MAX:
        return f"{name}{suffix}"
    room = NAME_MAX - len(suffix)
    words = name.split(" ")
    kept = ""
    for word in words:
        candidate = f"{kept} {word}".strip()
        if len(candidate) > room:
            break
        kept = candidate
    return f"{kept or name[:room].rstrip()}{suffix}"


async def _state_row(
    session: "AsyncSession", tenant_id: uuid.UUID, *, lock: bool = False
) -> OnboardingState | None:
    stmt = select(OnboardingState).where(OnboardingState.tenant_id == tenant_id)
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


async def _locked_state(
    session: "AsyncSession", tenant_id: uuid.UUID, started_by: str
) -> OnboardingState:
    """The workspace's onboarding row, locked `FOR UPDATE` (made first when it is missing).

    The new row is committed on its own before it is locked: a competing request that made it
    first just wins the insert and we lock theirs."""
    row = await _state_row(session, tenant_id, lock=True)
    if row is not None:
        return row
    session.add(
        OnboardingState(
            tenant_id=tenant_id,
            status="in_progress",
            step=1,
            started_by=started_by[:320],
            created_at=_now(),
            updated_at=_now(),
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
    row = await _state_row(session, tenant_id, lock=True)
    if row is None:  # the row vanished between two statements: a deleted workspace
        raise APIError(404, "That workspace was not found.", "no_workspace")
    return row


def _author(request: "Request") -> str:
    user = getattr(request.state, "user", None)
    email = getattr(user, "email", None)
    return str(email)[:320] if email else "api"


# ------------------------------------------------------------------ the in-process client


def make_client(request: "Request") -> Any:
    """The caller's own credential, replayed against the engine's own routes (D14)."""
    from callobuzz_rag.cob.tools.http import InternalClient
    from callobuzz_rag.widget.security import request_client_ip

    authorization = request.headers.get("authorization")
    headers: dict[str, str] = {}
    api_key = request.headers.get("x-api-key")
    if not authorization and api_key:
        headers["X-API-Key"] = api_key
    return InternalClient(
        request.app, authorization, client_ip=request_client_ip(request), headers=headers
    )


def _raise_for(response: Any) -> None:
    """Pass a failed inner route's own status, sentence and code through unchanged."""
    if response.ok:
        return
    body = response.body if isinstance(response.body, dict) else {}
    detail, code = body.get("detail"), body.get("code")
    code = code if isinstance(code, str) else "onboarding_failed"
    if not isinstance(detail, str):
        status = response.status if 400 <= response.status < 500 else 502
        raise APIError(status, STEP_FAILED, code)
    extra = {k: v for k, v in body.items() if k not in ("detail", "code")}
    raise APIError(response.status, detail, code, **extra)


# ------------------------------------------------------------------ start_website


def _desired_identity(
    body: StartOnboardingRequest, business_name: str, origin: str
) -> dict[str, Any]:
    card: dict[str, Any] = {"website": origin}
    if body.whatsapp:
        card["whatsapp"] = body.whatsapp
    if body.hours is not None:
        card["hours"] = [h.model_dump(mode="json") for h in body.hours]
    identity: dict[str, Any] = {
        "assistant_name": body.assistant_name or default_assistant_name(business_name),
        "business_name": business_name[:120],
        "card": card,
    }
    if body.business_type:
        identity["business_type"] = body.business_type
    if body.avatar_url:
        identity["avatar_url"] = body.avatar_url
    return identity


def _matches(published: dict[str, Any] | None, wanted: dict[str, Any]) -> bool:
    """The published identity already says everything `wanted` asks for."""
    if not published:
        return False
    have = (published.get("config") or {}).get("identity") or {}

    def covered(want: Any, got: Any) -> bool:
        if isinstance(want, dict):
            return isinstance(got, dict) and all(covered(v, got.get(k)) for k, v in want.items())
        return bool(want == got)

    return covered(wanted, have)


async def _step_kb(
    client: Any, session: "AsyncSession", tenant: Tenant, state: OnboardingState
) -> uuid.UUID:
    """The business's knowledge base: the one onboarding recorded, else its oldest, else a new
    one named after the business."""
    from callobuzz_rag.business.home import main_corpus
    from callobuzz_rag.db.models.corpus import Corpus

    if state.kb_id is not None:
        found = await session.execute(
            select(Corpus.id).where(Corpus.id == state.kb_id, Corpus.tenant_id == tenant.id)
        )
        if found.scalars().first() is not None:
            return state.kb_id
    existing = await main_corpus(session, tenant.id)
    if existing is not None:
        return existing.id
    response = await client.request("POST", "/api/v1/corpora", json={"name": tenant.name[:255]})
    _raise_for(response)
    return uuid.UUID(str(response.body["id"]))


async def _step_assistant(client: Any, kb_id: uuid.UUID, wanted: dict[str, Any]) -> None:
    """Make the published assistant say what the owner typed. A knowledge base that already has
    a published assistant keeps it (only identity and card are patched); the business type's
    template is applied only while the assistant is still the untouched first version."""
    base = f"/api/v1/corpora/{kb_id}/assistant"
    state = await client.request("GET", base)
    _raise_for(state)
    published = state.body.get("published")
    draft = state.body.get("draft")
    revision = int(draft["draft_revision"]) if draft else 0
    untouched = published is None or int(published.get("version") or 0) <= 1
    have_type = ((published or {}).get("config") or {}).get("identity", {}).get("business_type")
    no_assistant_yet = published is None and draft is None
    template = wanted.get("business_type") or (DEFAULT_TEMPLATE if no_assistant_yet else None)
    apply_template = bool(template and (no_assistant_yet or (untouched and template != have_type)))
    if not untouched:
        # A configured assistant keeps its type and the business name its owner chose: only the
        # assistant's name, picture and card change.
        have_name = bool(
            (((published or {}).get("config") or {}).get("identity") or {}).get("business_name")
        )
        wanted = {
            k: v
            for k, v in wanted.items()
            if k != "business_type" and not (k == "business_name" and have_name)
        }
    if not apply_template and _matches(published, wanted):
        return
    if apply_template:
        response = await client.request(
            "POST",
            f"{base}/draft/from-template",
            json={"template": template, "keep_identity": True, "base_revision": revision or None},
        )
        _raise_for(response)
        revision = int(response.body["draft_revision"])
    patched = await client.request(
        "PATCH",
        f"{base}/draft",
        json={"patch": {"identity": wanted}, "base_revision": revision or None},
    )
    _raise_for(patched)
    revision = int(patched.body["draft_revision"])
    published_now = await client.request(
        "POST", f"{base}/publish", json={"base_revision": revision}
    )
    _raise_for(published_now)


def _covers_site(key: dict[str, Any], scope: str) -> bool:
    for origin in key.get("allowed_origins") or []:
        try:
            if origin != "*" and site_scope(origin) == scope:
                return True
        except UrlError:
            continue
    return False


async def _step_widget_key(
    client: Any, kb_id: uuid.UUID, state: OnboardingState, origins: list[str], scope: str
) -> dict[str, Any]:
    """A key that already allows the site is reused (missing spellings added), else an existing
    key gets the origins added, else one is created. Adding origins is what starts learning
    (the widget-key routes call the website hook)."""
    base = f"/api/v1/corpora/{kb_id}/widget-keys"
    listed = await client.request("GET", base)
    _raise_for(listed)
    keys = [k for k in (listed.body or []) if k.get("is_active", True)]
    chosen: dict[str, Any] | None = None
    if state.widget_key_id is not None:
        chosen = next((k for k in keys if str(k["id"]) == str(state.widget_key_id)), None)
    chosen = chosen or next((k for k in keys if _covers_site(k, scope)), None)
    if chosen is None and keys:
        chosen = keys[0]
    if chosen is None:
        created = await client.request(
            "POST", base, json={"name": "Website", "allowed_origins": origins}
        )
        _raise_for(created)
        return dict(created.body)
    have = list(chosen.get("allowed_origins") or [])
    missing = [o for o in origins if o not in have]
    if not missing:
        return dict(chosen)
    updated = await client.request(
        "PATCH", f"{base}/{chosen['id']}", json={"allowed_origins": [*have, *missing]}
    )
    _raise_for(updated)
    return dict(updated.body)


async def _step_learning(client: Any, kb_id: uuid.UUID, scope: str, origin: str) -> None:
    """Starting a key makes its sites learn; a key that already existed may never have. Best
    effort: the wizard goes on, the Knowledge page shows the site."""
    try:
        overview = await client.request("GET", f"/api/v1/corpora/{kb_id}/website")
        if not overview.ok or not isinstance(overview.body, dict):
            return
        if any(s.get("scope") == scope for s in overview.body.get("sources") or []):
            return
        learnable = overview.body.get("unlearned_origins") or []
        start = next((o for o in learnable if _safe_scope(o) == scope), None)
        if start is not None:
            await client.request(
                "POST", f"/api/v1/corpora/{kb_id}/website/learn", json={"origin": start}
            )
    except Exception:
        logger.info("onboarding could not start website learning", exc_info=True)


def _safe_scope(origin: str) -> str | None:
    try:
        return site_scope(origin)
    except UrlError:
        return None


async def start_website(
    session: "AsyncSession",
    request: "Request",
    tenant_id: uuid.UUID,
    body: StartOnboardingRequest,
) -> OnboardingOut:
    """`POST /onboarding/website`: knowledge base, assistant, widget key and website learning.

    Idempotent and resumable (see the module docstring). A failing step keeps what already
    succeeded and raises that route's own error.
    """
    settings = request.app.state.settings
    origin = normalize_website(body.website)
    scope = site_scope(origin)
    try:
        await check_url(origin, settings.website_allow_private_networks)
    except BlockedURLError as exc:
        raise APIError(422, "We cannot reach that address from here.", "website_blocked") from exc
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise APIError(403, "There is no workspace here.", "no_workspace")

    state = await _locked_state(session, tenant_id, _author(request))
    first_time = state.widget_key_id is None
    client = make_client(request)
    wanted = _desired_identity(body, tenant.name, origin)
    failure: APIError | None = None
    key: dict[str, Any] | None = None
    try:
        kb_id = await _step_kb(client, session, tenant, state)
        state.kb_id = kb_id
        await _step_assistant(client, kb_id, wanted)
        key = await _step_widget_key(client, kb_id, state, allowed_origins_for(origin), scope)
        state.widget_key_id = uuid.UUID(str(key["id"]))
        await _step_learning(client, kb_id, scope, origin)
    except APIError as exc:
        failure = exc
    state.website = origin
    state.updated_at = _now()
    if failure is None:
        # TV5 brochure: step 2 is "add brochures" (3 install, 4 try)
        state.status, state.step, state.completed_at = "in_progress", max(state.step, 2), None
        if first_time:
            await record_audit(
                session,
                request,
                "onboarding.website_started",
                tenant_id=tenant_id,
                target_type="tenant",
                target_id=str(tenant_id),
                details={
                    "kb_id": str(state.kb_id),
                    "widget_key_id": str(state.widget_key_id),
                    "origin": origin,
                },
            )
    await session.commit()
    if failure is not None:
        raise failure
    return await get_state(session, request, tenant_id)


# ------------------------------------------------------------------ get / patch


async def connection(session: "AsyncSession", key: WidgetKey | None) -> ConnectionOut:
    """Has the assistant loaded on a site the key names, and on which look-alike sites it was
    refused (offered for allowing only when they are a www or scheme twin of an allowed site)."""
    if key is None:
        return ConnectionOut(status="waiting", origin=None, first_seen_at=None, refused=[])
    rows = (
        (
            await session.execute(
                select(WidgetOriginSeen)
                .where(WidgetOriginSeen.widget_key_id == key.id)
                .order_by(WidgetOriginSeen.first_seen_at)
            )
        )
        .scalars()
        .all()
    )
    literal = _literal_origins(key)
    allowed = [r for r in rows if r.allowed and r.origin in literal]
    refused = [
        RefusedOrigin(origin=r.origin, last_seen_at=r.last_seen_at)
        for r in sorted((r for r in rows if not r.allowed), key=lambda r: r.last_seen_at)
        if _is_twin(r.origin, literal)
    ]
    if allowed:
        first = allowed[0]
        return ConnectionOut(
            status="connected",
            origin=first.origin,
            first_seen_at=first.first_seen_at,
            refused=refused,
        )
    return ConnectionOut(status="waiting", origin=None, first_seen_at=None, refused=refused)


async def learning_progress(
    session: "AsyncSession", kb_id: uuid.UUID | None
) -> LearningProgressOut | None:
    if kb_id is None:
        return None
    sources = (
        (await session.execute(select(WebsiteSource).where(WebsiteSource.corpus_id == kb_id)))
        .scalars()
        .all()
    )
    if not sources:
        return None
    statuses = {s.status for s in sources}
    if statuses & {"discovering", "learning"}:
        status = "learning"
    else:
        status = next(
            (s for s in ("failed", "blocked", "paused", "idle") if s in statuses), "ready"
        )
    return LearningProgressOut(
        status=status,
        pages_learned=sum(s.pages_learned for s in sources),
        pages_found=sum(s.pages_found for s in sources),
        first_pass_done=all(s.first_pass_done_at is not None for s in sources),
    )


# TV5 brochure
async def files_summary(session: "AsyncSession", kb_id: uuid.UUID | None) -> FilesOut:
    """The uploaded files of the knowledge base (documents with a stored original, so not website
    pages, articles or the system records) and how many are finished. Best effort: a database
    problem shows no files rather than failing the wizard."""
    from callobuzz_rag.db.models.document import Document
    from callobuzz_rag.db.models.document_version import DocumentVersion

    if kb_id is None:
        return FilesOut(count=0, learned=0)
    try:
        ids = list(
            (
                await session.execute(
                    select(Document.id)
                    .where(Document.corpus_id == kb_id, Document.raw_storage_key.is_not(None))
                    .limit(1000)
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            return FilesOut(count=0, learned=0)
        learned = (
            await session.execute(
                select(func.count(func.distinct(DocumentVersion.document_id))).where(
                    DocumentVersion.document_id.in_(ids), DocumentVersion.status == "active"
                )
            )
        ).scalar_one()
    except Exception:
        logger.warning("Could not count the uploaded files of %s", kb_id, exc_info=True)
        return FilesOut(count=0, learned=0)
    return FilesOut(count=len(ids), learned=int(learned))


# /TV5 brochure


def snippet_for(request: "Request", key: WidgetKey) -> str:
    from callobuzz_rag.api.routes.widget_keys import engine_public_url

    return (
        f'<script src="{engine_public_url(request)}/widget.js" '
        f'data-key="{key.public_key}" async></script>'
    )


async def _owned(
    session: "AsyncSession", tenant_id: uuid.UUID, row: OnboardingState
) -> tuple[uuid.UUID | None, WidgetKey | None]:
    """The recorded knowledge base and widget key, only while they still belong to this
    workspace (an admin can move a knowledge base to another one)."""
    from callobuzz_rag.db.models.corpus import Corpus

    if row.kb_id is None:
        return None, None
    corpus = await session.get(Corpus, row.kb_id)
    if corpus is None or corpus.tenant_id != tenant_id:
        return None, None
    key = await session.get(WidgetKey, row.widget_key_id) if row.widget_key_id else None
    if key is not None and key.corpus_id != corpus.id:
        key = None
    return corpus.id, key


async def get_state(
    session: "AsyncSession", request: "Request", tenant_id: uuid.UUID
) -> OnboardingOut:
    row = await _state_row(session, tenant_id)
    if row is None:
        return OnboardingOut(
            step=1,
            status="new",
            website=None,
            kb_id=None,
            widget_key_id=None,
            snippet=None,
            connection=await connection(session, None),
            learning=None,
            files=FilesOut(count=0, learned=0),  # TV5 brochure
        )
    kb_id, key = await _owned(session, tenant_id, row)
    return OnboardingOut(
        step=row.step,
        status=row.status,
        website=row.website,
        kb_id=kb_id,
        widget_key_id=key.id if key is not None else None,
        snippet=snippet_for(request, key) if key is not None else None,
        connection=await connection(session, key),
        learning=await learning_progress(session, kb_id),
        files=await files_summary(session, kb_id),  # TV5 brochure
    )


async def update_state(
    session: "AsyncSession",
    request: "Request",
    tenant_id: uuid.UUID,
    body: UpdateOnboardingRequest,
) -> OnboardingOut:
    """`PATCH /onboarding`: move to a step, skip the wizard, or finish it."""
    row = await _locked_state(session, tenant_id, _author(request))
    if body.step is not None:
        row.step = body.step
    if body.status is not None:
        row.status = body.status
        row.completed_at = _now()
    row.updated_at = _now()
    await session.commit()
    return await get_state(session, request, tenant_id)


# ------------------------------------------------------------------ Home


async def home_onboarding(session: "AsyncSession", tenant_id: uuid.UUID) -> Any:
    """The workspace's first-run state for Home: `new` when it never started."""
    from callobuzz_rag.api.schemas.home import HomeOnboarding

    row = await _state_row(session, tenant_id)
    if row is None:
        return HomeOnboarding(status="new", step=1)
    return HomeOnboarding(status=row.status, step=row.step)


async def site_connected(session: "AsyncSession", kb_id: uuid.UUID | None) -> bool:
    """The assistant of this knowledge base has loaded on a site its key names (not one that
    only a `*` let in)."""
    if kb_id is None:
        return False
    keys = (
        (await session.execute(select(WidgetKey).where(WidgetKey.corpus_id == kb_id)))
        .scalars()
        .all()
    )
    literal = {k.id: _literal_origins(k) for k in keys}
    seen = (
        (
            await session.execute(
                select(WidgetOriginSeen).where(
                    WidgetOriginSeen.corpus_id == kb_id, WidgetOriginSeen.allowed.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    return any(r.origin in literal.get(r.widget_key_id, set()) for r in seen)


# ------------------------------------------------------------------ suggested first questions


async def suggestions_for(session: "AsyncSession", kb_id: uuid.UUID, lang: str) -> list[str]:
    """The questions `GET .../website/suggestions` shows: from the best-ranked learned pages
    and whether the published business card has opening hours. No model."""
    from callobuzz_rag.db.models.assistant_config import STATUS_PUBLISHED, AssistantConfigVersion
    from callobuzz_rag.db.models.website import WebsitePage
    from callobuzz_rag.website.suggest import suggest

    rows = (
        (
            await session.execute(
                select(WebsitePage)
                .where(WebsitePage.corpus_id == kb_id, WebsitePage.status == "learned")
                .order_by(WebsitePage.rank_score.desc())
                .limit(60)
            )
        )
        .scalars()
        .all()
    )
    published = (
        (
            await session.execute(
                select(AssistantConfigVersion).where(
                    AssistantConfigVersion.corpus_id == kb_id,
                    AssistantConfigVersion.status == STATUS_PUBLISHED,
                )
            )
        )
        .scalars()
        .first()
    )
    card = ((published.config if published else None) or {}).get("identity", {}).get("card", {})
    return suggest(
        [(p.url, p.title, p.rank_score) for p in rows],
        card_hours=bool(card.get("hours")),
        lang=lang,
    )
