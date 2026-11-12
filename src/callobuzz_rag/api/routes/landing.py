"""The public website: HTML pages outside `/api` (LP1).

`GET /`, `/pricing`, `/features`, `/about`, `/contact` and the same under `/hi/` and `/hi-latn/`.
While the site is off (or nothing is published) every path answers 404 with `X-Cob-Landing: off`;
Caddy then hands the request to the dashboard, so switching the site on or off needs no reload.
Not in the OpenAPI spec. Nothing here reads the request's `Host`: canonical links, alternates
and the sitemap come from the configured address.
"""

import hashlib
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Final
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import select

from callobuzz_rag.api.errors import APIError  # LP3 landing
from callobuzz_rag.core.exceptions import AccountsNotConfiguredError
from callobuzz_rag.db.models.landing import LandingAsset, LandingVersion
from callobuzz_rag.landing import assets, build, contact, og, page_extra, render, seo, store, ui
from callobuzz_rag.landing.cache import PUBLISHED, PublishedSite
from callobuzz_rag.landing.csp import build_csp
from callobuzz_rag.landing.environment import ENVIRONMENT, SiteEnvironment
from callobuzz_rag.landing.preview import PreviewTokenError, read_preview_token
from callobuzz_rag.landing.schema import LANG_PREFIX, LandingContent
from callobuzz_rag.landing.theme import ThemeTokens, theme_css

logger = logging.getLogger(__name__)

router = APIRouter(tags=["landing"], include_in_schema=False)

_BASE_HEADERS: Final = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
_OFF_BODY: Final = "<!doctype html><title>Not found</title><p>Not found</p>"


# ------------------------------------------------------------------ the two reads tests replace


async def get_live_site(request: Request) -> PublishedSite | None:
    return await PUBLISHED.get(request.app.state.session_factory, time.monotonic())


async def get_environment(
    request: Request, *, address: str | None, ask_widget_key_id: object
) -> SiteEnvironment:
    return await ENVIRONMENT.get(
        request.app.state.session_factory,
        request.app.state.settings,
        site_address=address,
        ask_widget_key_id=ask_widget_key_id,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ responses


def off_response() -> Response:
    return Response(
        _OFF_BODY,
        status_code=404,
        media_type="text/html; charset=utf-8",
        headers={**_BASE_HEADERS, "X-Cob-Landing": "off", "Cache-Control": "no-store"},
    )


def _page_headers(
    rendered: render.RenderedPage, *, preview: bool, dashboard: list[str]
) -> dict[str, str]:
    return {
        **_BASE_HEADERS,
        "Content-Security-Policy": build_csp(
            rendered.inline_scripts, preview=preview, dashboard_origins=dashboard
        ),
    }


def _etag(
    site: PublishedSite, env: SiteEnvironment, page: str, lang: str, extra: object = None
) -> str:
    material = f"{site.version}|{site.content_hash}|{page}|{lang}|{env!r}|{extra!r}"
    return '"' + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32] + '"'


def _not_modified(request: Request, etag: str) -> bool:
    sent = request.headers.get("if-none-match", "")
    return any(part.strip().removeprefix("W/") == etag for part in sent.split(","))


def _enabled(content: LandingContent, page: str) -> bool:
    if page == "home":
        return True
    return bool(getattr(content, page).enabled)


def _dashboard_origins(request: Request) -> list[str]:
    parts = urlsplit(str(request.app.state.settings.dashboard_url))
    return [f"{parts.scheme}://{parts.netloc}"] if parts.netloc else []


def _not_found(
    content: LandingContent, tokens: ThemeTokens, env: SiteEnvironment, lang: str, *, preview: bool
) -> Response:
    ctx = render.PageContext(
        content=content,
        page="404",
        lang=lang,
        theme_css=theme_css(tokens, content.theme.base),
        env=env,
        preview=preview,
        extra={"noindex": True},
    )
    rendered = render.render_page(ctx, template="pages/not_found.html")
    return HTMLResponse(
        rendered.html,
        status_code=404,
        headers={
            **_page_headers(rendered, preview=False, dashboard=[]),
            "Cache-Control": "no-store",
        },
    )


# ------------------------------------------------------------------ published pages


def _page_handler(page: str, lang: str) -> Callable[[Request], Awaitable[Response]]:
    async def handler(request: Request) -> Response:
        site = await get_live_site(request)
        if site is None:
            return off_response()
        env = await get_environment(
            request, address=site.address, ask_widget_key_id=site.ask_widget_key_id
        )
        if lang not in site.content.languages or not _enabled(site.content, page):
            return _not_found(
                site.content, site.theme, env, site.content.default_language, preview=False
            )
        # LP3 landing: the pricing page follows its query; the contact page carries a fresh signed
        # form time, so it is never cached or answered 304.
        extra = page_extra.for_page(page, request.query_params)
        if page == "contact":
            extra = await contact.page_state(request, request.query_params)
        etag = _etag(site, env, page, lang, None if page == "contact" else extra)
        headers = {"ETag": etag, "Cache-Control": "public, max-age=60"}
        if page == "contact":
            headers = {"Cache-Control": "no-store"}
        elif _not_modified(request, etag):
            return Response(status_code=304, headers={**headers, **_BASE_HEADERS})
        # /LP3 landing

        ctx = render.PageContext(
            content=site.content,
            page=page,
            lang=lang,
            theme_css=site.theme_css,
            env=env,
            version=site.version,
            jsonld=seo.jsonld_blocks(site.content, env, page, lang),
            og_image=seo.og_image_url(env, site.version, lang),
            extra=extra,  # LP3 landing
        )
        rendered = render.render_page(
            ctx,
            template="pages/contact_done.html" if extra.get("sent") else None,  # LP3 landing
        )
        return HTMLResponse(
            rendered.html,
            headers={**headers, **_page_headers(rendered, preview=False, dashboard=[])},
        )

    return handler


def _register() -> None:
    for lang, prefix in LANG_PREFIX.items():
        for page, tail in render.PAGE_PATHS.items():
            paths = [f"{prefix}{tail}" or "/"]
            if page == "home" and prefix:
                paths = [prefix, prefix + "/"]
            for path in paths:
                router.add_api_route(
                    path,
                    _page_handler(page, lang),
                    methods=["GET"],
                    name=f"landing_{page}_{lang}",
                    response_class=HTMLResponse,
                )


_register()


# LP3 landing: the contact form without JavaScript. The page's script posts JSON to
# `/api/v1/public/landing/contact` instead; both share `contact.submit`.

_FORM_LIMIT: Final = 20_000
_FORM_FIELDS: Final = ("name", "email", "phone", "topic", "message", "website", "service")


def _form_payload(raw: bytes, lang: str) -> dict[str, object]:
    parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    fields = {key: values[0] for key, values in parsed.items() if values}
    payload: dict[str, object] = {name: fields.get(name, "") for name in _FORM_FIELDS}
    for optional in ("email", "phone", "service"):
        payload[optional] = str(payload[optional]).strip() or None
    payload["form_token"] = fields.get("form_token", "")
    payload["consent"] = bool(fields.get("consent"))
    payload["lang"] = lang
    return payload


async def _read_limited(request: Request) -> bytes | None:
    """The body, or None once it passes `_FORM_LIMIT`; never buffers more than that."""
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > _FORM_LIMIT:
        return None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _FORM_LIMIT:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _form_handler(lang: str) -> Callable[[Request], Awaitable[Response]]:
    async def handler(request: Request) -> Response:
        site = await get_live_site(request)
        if site is None:
            return off_response()
        env = await get_environment(
            request, address=site.address, ask_widget_key_id=site.ask_widget_key_id
        )
        content = site.content
        if lang not in content.languages or not content.contact.enabled:
            return _not_found(content, site.theme, env, content.default_language, preview=False)
        raw = await _read_limited(request)
        if raw is None:
            return Response("Too large", status_code=413, media_type="text/plain")
        payload = _form_payload(raw, lang)
        words = ui.strings(lang)
        errors: dict[str, str] = {}
        message: str | None = None
        status = 422
        extra_headers: dict[str, str] = {}
        try:
            await contact.submit(request, payload, field_errors=True)
        except ValidationError as exc:
            for item in exc.errors():
                field = str(item["loc"][0]) if item["loc"] else "email"
                if field in ("email", "phone") and item["type"] == "value_error":
                    errors["email"] = words["contact_phone_or_email"]
                elif field == "consent":
                    errors["consent"] = words["contact_required"]
                elif field in _FORM_FIELDS:
                    errors[field] = words["contact_required"]
                else:
                    message = words["contact_invalid"]
        except APIError as exc:
            status = exc.status_code
            if exc.code == "landing_contact_rate_limited":
                extra_headers = {k: v for k, v in (exc.headers or {}).items() if k == "Retry-After"}
            message = {
                "landing_contact_rate_limited": words["contact_rate_limited"],
                "landing_contact_unavailable": words["contact_unavailable"],
            }.get(exc.code, words["contact_invalid"])
        else:
            return RedirectResponse(f"{render.page_path('contact', lang)}?sent=1", status_code=303)
        state = await contact.page_state(request, {})
        state.update(
            form_values={k: v for k, v in payload.items() if k in _FORM_FIELDS and k != "website"},
            form_errors=errors,
            form_message=message,
            topic=str(payload["topic"]) if payload["topic"] else None,
        )
        ctx = render.PageContext(
            content=content,
            page="contact",
            lang=lang,
            theme_css=site.theme_css,
            env=env,
            version=site.version,
            extra=state,
        )
        rendered = render.render_page(ctx)
        return HTMLResponse(
            rendered.html,
            status_code=status,
            headers={
                **_page_headers(rendered, preview=False, dashboard=[]),
                "Cache-Control": "no-store",
                **extra_headers,
            },
        )

    return handler


def _register_forms() -> None:
    for lang, prefix in LANG_PREFIX.items():
        router.add_api_route(
            f"{prefix}/contact",
            _form_handler(lang),
            methods=["POST"],
            name=f"landing_contact_form_{lang}",
            response_class=HTMLResponse,
        )


_register_forms()
# /LP3 landing


# ------------------------------------------------------------------ the draft, by signed link


@router.get("/_landing/preview/{page}")
async def preview_page(page: str, request: Request, t: str = "", lang: str = "en") -> Response:
    """The unpublished draft, for the editor's iframe. The signed token names the page and the
    language; nothing is cached and search engines are told to stay away."""
    settings = request.app.state.settings
    noindex = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}

    def expired() -> Response:
        return HTMLResponse(
            render.render_expired(lang if lang in LANG_PREFIX else "en"),
            status_code=403,
            headers={**_BASE_HEADERS, **noindex, "Content-Security-Policy": build_csp([])},
        )

    try:
        claims = read_preview_token(settings, t, now=datetime.now(UTC))
    except (PreviewTokenError, AccountsNotConfiguredError):
        return expired()
    if claims.page != page or claims.lang != lang or page not in render.PAGE_PATHS:
        return expired()
    async with request.app.state.session_factory() as session:
        site = await store.ensure_site(session)
        await session.commit()
        content = LandingContent.model_validate(site.draft)
        tokens = ThemeTokens.from_json(site.draft_theme)
        address, ask_key = site.address, site.ask_widget_key_id
    env = await get_environment(request, address=address, ask_widget_key_id=ask_key)
    if lang not in content.languages or not _enabled(content, page):
        response = _not_found(content, tokens, env, content.default_language, preview=True)
        response.headers.update(noindex)
        return response
    ctx = render.PageContext(
        content=content,
        page=page,
        lang=lang,
        theme_css=theme_css(tokens, content.theme.base),
        env=env,
        preview=True,
        media_token=t,
    )
    rendered = render.render_page(ctx)
    return HTMLResponse(
        rendered.html,
        headers={
            **_page_headers(rendered, preview=True, dashboard=_dashboard_origins(request)),
            **noindex,
        },
    )


# ------------------------------------------------------------------ robots, sitemap, share image

_TEXT_HEADERS: Final = {**_BASE_HEADERS, "Cache-Control": "public, max-age=3600"}


async def _site_and_env(request: Request) -> tuple[PublishedSite, SiteEnvironment] | None:
    site = await get_live_site(request)
    if site is None:
        return None
    env = await get_environment(
        request, address=site.address, ask_widget_key_id=site.ask_widget_key_id
    )
    return site, env


@router.get("/robots.txt")
async def robots(request: Request) -> Response:
    found = await _site_and_env(request)
    if found is None:
        return off_response()
    return Response(
        seo.robots_txt(found[1].address),
        media_type="text/plain; charset=utf-8",
        headers=_TEXT_HEADERS,
    )


@router.get("/sitemap.xml")
async def sitemap(request: Request) -> Response:
    found = await _site_and_env(request)
    if found is None:
        return off_response()
    site, env = found
    if not env.address:  # no configured address: absolute URLs cannot be built, so none are made
        return Response(
            seo.robots_txt(None), status_code=404, media_type="text/plain; charset=utf-8"
        )
    body = seo.sitemap_xml(
        site.content, env.address, site.published_at, [slug for slug, _ in env.legal_links]
    )
    return Response(body, media_type="application/xml; charset=utf-8", headers=_TEXT_HEADERS)


_OG_NAME: Final = re.compile(r"^(\d{1,9})-(en|hi|hi-Latn)\.png$")
_MEDIA_NAME: Final = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.(png|webp|mp4|webm)$"
)
_FILE_HEADERS: Final = {**_BASE_HEADERS, **assets.MEDIA_HEADERS}
_IMMUTABLE: Final = "public, max-age=31536000, immutable"


def _plain_404() -> Response:
    return Response("Not found", status_code=404, media_type="text/plain; charset=utf-8")


@router.get("/_landing/og/{name}")
async def og_image(name: str, request: Request) -> Response:
    """The share image of a version, drawn on the first request and kept."""
    found = await _site_and_env(request)
    if found is None:
        return off_response()
    match = _OG_NAME.match(name)
    if match is None:
        return _plain_404()
    number, lang = int(match.group(1)), match.group(2)
    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        version = (
            await session.execute(select(LandingVersion).where(LandingVersion.version == number))
        ).scalar_one_or_none()
        if version is None:
            return _plain_404()
        asset = await og.ensure_image(
            session,
            settings,
            version=version,
            lang=lang,
            product_name=found[1].branding.product_name,
        )
        await session.commit()
    path = await assets.ensure_cached(settings, asset) if asset is not None else None
    if path is None:
        return _plain_404()
    return FileResponse(
        path,
        media_type="image/png",
        headers={**_FILE_HEADERS, "Cache-Control": _IMMUTABLE},
    )


@router.get("/_landing/media/{name}")
async def media(name: str, request: Request, t: str = "") -> Response:
    """An uploaded logo, poster or video. A published page may use only what the published
    version names; the editor's preview proves itself with its signed token."""
    settings = request.app.state.settings
    match = _MEDIA_NAME.match(name)
    previewing = False
    if t:
        try:
            read_preview_token(settings, t, now=datetime.now(UTC))
            previewing = True
        except (PreviewTokenError, AccountsNotConfiguredError):
            previewing = False
    if not previewing:
        site = await get_live_site(request)
        if site is None:
            return off_response()
        if match is None or match.group(1) not in site.asset_ids:
            return _plain_404()
    if match is None:
        return _plain_404()
    async with request.app.state.session_factory() as session:
        asset = await session.get(LandingAsset, uuid.UUID(match.group(1)))
    if asset is None or asset.kind == "og" or store.media_name(asset) != name:
        return _plain_404()
    path = await assets.ensure_cached(settings, asset)
    if path is None:
        return _plain_404()
    cache = "no-store" if previewing else _IMMUTABLE
    return FileResponse(
        path, media_type=asset.content_type, headers={**_FILE_HEADERS, "Cache-Control": cache}
    )


# ------------------------------------------------------------------ the front-end build

_BUILD_TYPES: Final = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".json": "application/json",
}


@router.get("/_landing/build/{file:path}")
async def build_asset(file: str) -> Response:
    """A hashed script, style or font of the website. Served whether or not the site is on (the
    preview needs them), and only names the build manifest lists."""
    path = build.build_file(file)
    if path is None:
        return _plain_404()
    return FileResponse(
        path,
        media_type=_BUILD_TYPES.get(path.suffix, "application/octet-stream"),
        headers={**_BASE_HEADERS, "Cache-Control": _IMMUTABLE},
    )
