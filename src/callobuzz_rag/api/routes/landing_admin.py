"""The operator's editor API for the public website (LP1).

Operator only (`require_admin`): workspace sessions and `tk_`/`ck_` keys are refused. Draft saves
are not audited; settings, publishing and restoring are. The public pages are served by
`landing.py`.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.api.deps import get_session, get_settings, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.landing import (
    LandingAskInfo,
    LandingAssetOut,
    LandingConnectAssistantRequest,  # LP4 landing
    LandingContrastRow,
    LandingCopyWarning,
    LandingGenerateOut,
    LandingGenerateRequest,
    LandingGenerateSource,
    LandingPresetInfo,
    LandingPresetList,
    LandingPresetOut,
    LandingPreviewLinkOut,
    LandingPreviewLinkRequest,
    LandingPublishedInfo,
    LandingPublishRequest,
    LandingRestoreRequest,
    LandingSaveDraftRequest,
    LandingSettingsRequest,
    LandingState,
    LandingThemePreview,
    LandingThemePreviewRequest,
    LandingVersionList,
    LandingVersionOut,
    LandingWarning,
)
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.landing import LandingAsset
from callobuzz_rag.landing import assets, presets, service, store
from callobuzz_rag.landing.cache import invalidate_all
from callobuzz_rag.landing.copy_rules import copy_warnings
from callobuzz_rag.landing.preview import TTL as PREVIEW_TTL
from callobuzz_rag.landing.preview import make_preview_token
from callobuzz_rag.landing.schema import LandingContent, LandingLang, text_limits
from callobuzz_rag.landing.theme import ThemeTokens, derive_theme, theme_css

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/landing", tags=["landing"], dependencies=[Depends(require_admin)]
)


def _theme_out(tokens: ThemeTokens, base: str) -> LandingThemePreview:
    return LandingThemePreview(
        light=tokens.light,
        dark=tokens.dark,
        report=[LandingContrastRow(**vars(row)) for row in tokens.report],
        adjusted=tokens.adjusted,
        css=theme_css(tokens, base),
    )


def _asset_out(a: LandingAsset) -> LandingAssetOut:
    return LandingAssetOut(
        id=a.id,
        kind=a.kind,
        content_type=a.content_type,
        bytes=a.bytes,
        width=a.width,
        height=a.height,
        created_at=a.created_at,
        url=f"/_landing/media/{store.media_name(a)}",
    )


async def _state(session: AsyncSession, settings: Settings) -> LandingState:
    site = await store.ensure_site(session)
    content = LandingContent.model_validate(site.draft)
    address = service.effective_address(site.address, settings.public_url)
    status = await service.ask_status(session, site, service.origin_of(address))
    published = (
        await store.get_version(session, site.published_version)
        if site.published_version is not None
        else None
    )
    found = await service.warnings(session, site, content, address=address, ask=status)
    return LandingState(
        enabled=bool(site.enabled),
        address=site.address,
        address_effective=address,
        draft=content,
        draft_revision=site.draft_revision,
        draft_theme=_theme_out(ThemeTokens.from_json(site.draft_theme), content.theme.base),
        published=(
            LandingPublishedInfo(
                version=published.version,
                published_at=published.published_at,
                published_by=published.published_by,
            )
            if published is not None
            else None
        ),
        ask=LandingAskInfo(
            corpus_id=site.ask_corpus_id, widget_key_id=site.ask_widget_key_id, status=status
        ),
        warnings=[LandingWarning(code=w.code, message=w.message) for w in found],
        copy_warnings=[
            LandingCopyWarning(path=w.path, word=w.word, sentence=w.sentence)
            for w in copy_warnings(content)
        ],
        assets=[_asset_out(a) for a in await store.list_assets(session)],
        # LP4 landing: the largest upload, so the editor can check a file before sending it
        limits={
            **text_limits(),
            "upload.video_mb": settings.landing_video_max_mb,
            "upload.image_mb": settings.landing_image_max_mb,
        },
        # /LP4 landing
    )


def _label(request: Request) -> str:
    return actor_from_request(request)[2]


@router.get("", response_model=LandingState)
async def get_landing(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)
) -> LandingState:
    """The draft, what is published, the switch and everything the editor needs to show."""
    state = await _state(session, settings)
    await session.commit()  # the site row is made the first time it is read
    return state


@router.put("/draft", response_model=LandingState)
async def put_draft(
    body: LandingSaveDraftRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LandingState:
    """Save the draft. `base_revision` is the revision you loaded; a stale one answers 409
    `landing_revision_conflict`."""
    await service.save_draft(
        session,
        base_revision=body.base_revision,
        content=body.content,
        updated_by=_label(request),
    )
    state = await _state(session, settings)
    await session.commit()
    return state


@router.put("/settings", response_model=LandingState)
async def put_settings(
    body: LandingSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LandingState:
    """Switch the public website on or off and set its address. Switching on needs a published
    version and an address."""
    result = await service.update_settings(
        session,
        enabled=body.enabled,
        address=body.address,
        address_given="address" in body.model_fields_set,
        public_url=settings.public_url,
        prod=settings.is_prod,
        updated_by=_label(request),
    )
    if result.changed:
        await record_audit(
            session,
            request,
            "landing.settings_updated",
            target_type="landing",
            target_id="site",
            details={"changed": sorted(result.changed)},
        )
    state = await _state(session, settings)
    await session.commit()
    invalidate_all()
    return state


@router.post("/theme/preview", response_model=LandingThemePreview)
async def preview_theme(body: LandingThemePreviewRequest) -> LandingThemePreview:
    """The palette a brand colour produces, with the measured contrast of every text pair.
    Pure: nothing is saved."""
    return _theme_out(derive_theme(body.brand_color, body.accent_color), body.base)


@router.post("/publish", response_model=LandingState)
async def publish(
    body: LandingPublishRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LandingState:
    """Freeze the draft as the next version. Every language the site offers must be complete."""
    row = await service.publish(
        session,
        base_revision=body.base_revision,
        change_note=body.change_note,
        published_by=_label(request),
    )
    await record_audit(
        session,
        request,
        "landing.published",
        target_type="landing",
        target_id="site",
        details={"version": row.version},
    )
    await assets.prune_unreferenced(session, settings)
    state = await _state(session, settings)
    await session.commit()
    invalidate_all()
    return state


@router.get("/versions", response_model=LandingVersionList)
async def list_versions(session: AsyncSession = Depends(get_session)) -> LandingVersionList:
    """Published versions, newest first (the last 50)."""
    rows = (await store.all_versions(session))[: store.VERSIONS_SHOWN]
    return LandingVersionList(
        items=[
            LandingVersionOut(
                version=r.version,
                published_at=r.published_at,
                published_by=r.published_by,
                change_note=r.change_note,
            )
            for r in rows
        ]
    )


@router.post("/versions/{version}/restore", response_model=LandingState)
async def restore_version(
    version: int,
    body: LandingRestoreRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LandingState:
    """Copy a published version into the draft. Publishing it again is a separate step."""
    await service.restore(
        session, version=version, base_revision=body.base_revision, updated_by=_label(request)
    )
    await record_audit(
        session,
        request,
        "landing.version_restored",
        target_type="landing",
        target_id="site",
        details={"version": version},
    )
    state = await _state(session, settings)
    await session.commit()
    return state


@router.post("/preview-link", response_model=LandingPreviewLinkOut)
async def preview_link(
    body: LandingPreviewLinkRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LandingPreviewLinkOut:
    """A signed link that shows the draft of one page and language for 15 minutes. The page is
    `noindex`, never cached, and can be framed by the dashboard only."""
    now = datetime.now(UTC)
    token = make_preview_token(settings, page=body.page, lang=body.lang, now=now)
    site = await store.ensure_site(session)
    base = service.effective_address(site.address, settings.public_url)
    # Never the address the request arrived on. Without a configured address a development machine
    # points at its own engine port; in production the link is relative to the dashboard's origin,
    # which the reverse proxy routes to the engine.
    root = base or ("" if settings.is_prod else f"http://127.0.0.1:{settings.api_port}")
    query = urlencode({"t": token, "lang": body.lang})
    return LandingPreviewLinkOut(
        url=f"{root}/_landing/preview/{body.page}?{query}", expires_at=now + PREVIEW_TTL
    )


@router.post("/assets", response_model=LandingAssetOut)
async def upload_asset(
    request: Request,
    kind: Annotated[Literal["logo", "poster", "video"], Form()],
    file: Annotated[UploadFile, File()],
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LandingAssetOut:
    """Add a logo, a poster picture or a video. Pictures are re-encoded (PNG or WebP, never
    SVG); a video must be MP4 or WebM and is stored as it is."""
    megabytes = settings.landing_video_max_mb if kind == "video" else settings.landing_image_max_mb
    limit = megabytes * 1024 * 1024
    data = await assets.read_limited(file, limit)
    if len(data) > limit:
        raise assets.too_large(settings)
    size: tuple[int, int] | None = None
    if kind == "video":
        content_type = assets.sniff_video(data)
    else:
        data, content_type, size = await asyncio.to_thread(assets.reencode_picture, data, kind=kind)
    asset = await assets.put_asset(
        session,
        settings,
        kind=kind,
        content_type=content_type,
        data=data,
        size=size,
        created_by=_label(request),
    )
    await record_audit(
        session,
        request,
        "landing.asset_uploaded",
        target_type="landing",
        target_id=str(asset.id),
        details={"kind": kind, "bytes": asset.bytes},
    )
    await session.commit()
    return _asset_out(asset)


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Remove an upload nothing uses. A file the draft or any published version names is kept
    (409 `landing_asset_in_use`)."""
    asset = await session.get(LandingAsset, asset_id)
    if asset is None or asset.kind == "og":
        raise APIError(404, "That file was not found.", "landing_asset_not_found")
    draft, published = await assets.referenced_ids(session)
    if str(asset.id) in draft | published:
        raise APIError(
            409,
            "This file is used on your website. Remove it from the page first.",
            "landing_asset_in_use",
        )
    kind = asset.kind
    await assets.delete_asset(session, settings, asset)
    await record_audit(
        session,
        request,
        "landing.asset_deleted",
        target_type="landing",
        target_id=str(asset_id),
        details={"kind": kind},
    )
    await session.commit()
    return Response(status_code=204)


# LP2 landing
@router.get("/presets", response_model=LandingPresetList)
async def list_presets() -> LandingPresetList:
    """The six starting examples (no AI cost)."""
    return LandingPresetList(
        items=[
            LandingPresetInfo(key=p.key, name=p.name, languages=list(p.languages))
            for p in presets.all_presets()
        ]
    )


@router.get("/presets/{key}", response_model=LandingPresetOut)
async def get_preset(key: str, lang: LandingLang = "en") -> LandingPresetOut:
    """One starting example in one language: the hero conversation and the industry sections."""
    try:
        preset = presets.load_preset(key)
    except presets.PresetNotFoundError:
        raise APIError(
            404, "That starting example does not exist.", "landing_preset_not_found"
        ) from None
    return LandingPresetOut(
        key=preset.key,
        lang=lang,
        conversation=preset.conversation(lang),
        sections=preset.sections(lang),
    )


@router.post("/generate", response_model=LandingGenerateOut)
async def generate_conversation(
    body: LandingGenerateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LandingGenerateOut:
    """Write a draft hero conversation from one of the operator's own knowledge bases.

    One model call (the knowledge base's own model, the operator's plan profile). The reply is
    returned for review; the website draft is never touched. The spend is metered under the
    usage source `landing` and never counts as a question; whatever the call spent is
    metered, even when its reply turns out to be unusable.
    """
    from callobuzz_rag.analytics.usage import SOURCE_LANDING, payer_of, record_usage
    from callobuzz_rag.api.routes.query import _get_config_resolver
    from callobuzz_rag.billing.context import mark_admin_answer
    from callobuzz_rag.core.exceptions import LLMError
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant
    from callobuzz_rag.landing import generate
    from callobuzz_rag.llm.cost import request_cost_of
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.tiers import profiles as tier_profiles
    from callobuzz_rag.tiers.resolve import resolve_with_profile

    corpus = await session.get(Corpus, body.corpus_id)
    tenant = await session.get(Tenant, corpus.tenant_id) if corpus is not None else None
    if corpus is None or tenant is None or not tenant.is_operator:
        raise APIError(
            409,
            "Choose one of your own knowledge bases (not a customer's).",
            "landing_not_operator_kb",
        )
    context = await generate.gather_context(session, corpus.id, body.preset)
    if context.empty:
        raise APIError(
            409,
            "This knowledge base has nothing to read yet. Add your website or a brochure first.",
            "landing_generate_no_content",
        )

    settings = request.app.state.settings
    resolved, _retrieval = resolve_with_profile(
        _get_config_resolver(request),
        corpus,
        headers=None,
        profile=await tier_profiles.plan_profile(session, corpus.tenant_id, settings=settings),
        settings=settings,
    )
    llm = LiteLLMProvider.from_resolved_config(resolved)
    if not llm.has_credentials:
        raise APIError(
            503,
            "No AI model is set up for this knowledge base, so a conversation can't be written.",
            "landing_generate_unavailable",
        )

    messages = generate.build_prompt(context, language=body.language, preset=body.preset)
    reply = None
    try:
        reply = await llm.complete_json(messages, **llm.fast_call_options())
    except LLMError:
        logger.warning("Landing generate model call failed for %s", corpus.id, exc_info=True)
    cost = request_cost_of(llm)
    if cost is not None:
        mark_admin_answer(request)  # the operator's own helper call is never billed
        await record_usage(
            session,
            corpus_id=corpus.id,
            tenant_id=corpus.tenant_id,
            source=SOURCE_LANDING,
            prompt_tokens=0,
            completion_tokens=0,
            model=getattr(resolved, "model", None),
            requests=0,  # spend, not a question
            cost=cost,
            payer=payer_of(resolved),
        )
    failed = APIError(502, "The AI model did not answer. Try again.", "landing_generate_failed")
    if reply is None:
        raise failed
    try:
        conversation = generate.parse_reply(
            reply.content, context, language=body.language, preset=body.preset
        )
    except generate.GenerateError:
        logger.warning("Landing generate reply was not usable for %s", corpus.id, exc_info=True)
        raise failed from None
    await record_audit(
        session,
        request,
        "landing.generated",
        target_type="landing",
        target_id="site",
        details={"corpus_id": str(corpus.id), "language": body.language},
    )
    await session.commit()
    return LandingGenerateOut(
        conversation=conversation,
        sources=[
            LandingGenerateSource(**item) for item in generate.sources_of(conversation, context)
        ],
        cost_note=(
            "Written with one AI call from your own content. It is a draft: nothing on your "
            "website changed."
        ),
    )


# /LP2 landing


# LP4 landing
@router.post("/assistant", response_model=LandingState)
async def connect_assistant(
    body: LandingConnectAssistantRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LandingState:
    """Point "Ask it yourself" at one of the operator's own knowledge bases. A key of that
    knowledge base that already allows the website's address is reused; otherwise one key named
    "Public website" is made through the widget-key route with your own sign-in. No other key is
    ever edited."""
    from callobuzz_rag.landing import assistant_link
    from callobuzz_rag.onboarding.service import make_client

    site = await store.ensure_site(session)
    origin = service.origin_of(service.effective_address(site.address, settings.public_url))
    result = await assistant_link.connect(
        session,
        client=make_client(request),
        corpus_id=body.corpus_id,
        origin=origin,
        updated_by=_label(request),
    )
    await record_audit(
        session,
        request,
        "landing.assistant_connected",
        target_type="landing",
        target_id="site",
        details={"corpus_id": str(body.corpus_id), "key_id": str(result.key_id)},
    )
    state = await _state(session, settings)
    await session.commit()
    invalidate_all()
    return state


@router.delete("/assistant", response_model=LandingState)
async def disconnect_assistant(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LandingState:
    """Stop using the key. The key and the knowledge base are not changed."""
    from callobuzz_rag.landing import assistant_link

    await assistant_link.disconnect(session, updated_by=_label(request))
    await record_audit(
        session,
        request,
        "landing.assistant_disconnected",
        target_type="landing",
        target_id="site",
    )
    state = await _state(session, settings)
    await session.commit()
    invalidate_all()
    return state


# /LP4 landing
