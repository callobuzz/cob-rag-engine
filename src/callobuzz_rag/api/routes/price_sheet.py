"""The white-label price list (TV3): the provider's preview and share link, and the public page.

`GET /api/v1/admin/price-sheet` is the admin's own view (JSON, or the printable page);
`POST|DELETE /api/v1/admin/price-sheet/link` make a new public link (the old one stops working)
or remove it; `GET /api/v1/public/price-sheet/{token}` serves the page to anyone holding the link.
A price list is not a secret (D13), but the link is a token the provider can rotate: an unknown
or rotated token and a removed link all answer the same 404.
"""

import hmac
import secrets
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts import settings_store
from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.settings_store import read_engine_settings_row
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.widget_keys import engine_public_url
from callobuzz_rag.api.schemas.price_sheet import PriceSheetLinkOut, PriceSheetOut
from callobuzz_rag.billing import price_sheet as service

admin_router = APIRouter(
    prefix="/api/v1/admin/price-sheet", tags=["billing"], dependencies=[Depends(require_admin)]
)
public_router = APIRouter(prefix="/api/v1/public/price-sheet", tags=["billing"])

_FORMATS: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The price list as JSON or a printable HTML page.",
        "content": {"application/json": {}, "text/html": {}},
    }
}


def not_found() -> APIError:
    return APIError(404, "That price list link is not valid.", "price_sheet_not_found")


def _html(sheet: service.PriceSheet) -> Response:
    return Response(
        service.render_html(sheet),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": service.HTML_CSP,
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _respond(session: AsyncSession, fmt: str) -> Any:
    engine = (await read_engine_settings_row(session))[0]
    sheet = await service.build_sheet(
        session, engine.trust.price_sheet, engine.branding, invoicing=engine.invoicing
    )  # P2 invoices
    if fmt == "html":
        return _html(sheet)
    return PriceSheetOut.model_validate(service.as_dict(sheet))


@admin_router.get("", response_model=PriceSheetOut, responses=_FORMATS)
async def get_price_sheet(
    format: Annotated[Literal["json", "html"], Query()] = "json",
    session: AsyncSession = Depends(get_session),
) -> Any:
    """The price list as it stands: the plans you picked (or every active priced plan) and
    the add-ons, with your name, logo and colour. `format=html` is the printable page."""
    return await _respond(session, format)


@admin_router.post("/link", response_model=PriceSheetLinkOut, status_code=201)
async def rotate_price_sheet_link(
    request: Request, session: AsyncSession = Depends(get_session)
) -> PriceSheetLinkOut:
    """Make a new public link to the price list. The previous link stops working at once."""
    token = secrets.token_urlsafe(24)
    _, _, label = actor_from_request(request)
    await settings_store.set_price_sheet_token(session, token, actor_label=label)
    await record_audit(
        session,
        request,
        "settings.price_sheet_link_rotated",
        target_type="settings",
        target_id="price_sheet",
        details={},
    )
    await session.commit()
    settings_store.invalidate_engine_settings_cache()
    return PriceSheetLinkOut(url=f"{engine_public_url(request)}/api/v1/public/price-sheet/{token}")


@admin_router.delete("/link", status_code=204)
async def remove_price_sheet_link(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Remove the public link. Nothing is audited when there was none."""
    _, _, label = actor_from_request(request)
    previous = await settings_store.set_price_sheet_token(session, None, actor_label=label)
    if previous is not None:
        await record_audit(
            session,
            request,
            "settings.price_sheet_link_removed",
            target_type="settings",
            target_id="price_sheet",
            details={},
        )
    await session.commit()
    settings_store.invalidate_engine_settings_cache()
    return Response(status_code=204)


@public_router.get("/{token}", response_model=PriceSheetOut, responses=_FORMATS)
async def get_public_price_sheet(
    token: Annotated[str, Path(max_length=128)],
    format: Annotated[Literal["json", "html"], Query()] = "html",
    session: AsyncSession = Depends(get_session),
) -> Any:
    """The price list for whoever holds the link (printable HTML, or `format=json`). An unknown,
    rotated or removed link is the same 404."""
    engine = (await read_engine_settings_row(session))[0]
    stored = engine.trust.price_sheet.public_token
    if not stored or not hmac.compare_digest(stored.encode(), token.encode()):
        raise not_found()
    sheet = await service.build_sheet(
        session, engine.trust.price_sheet, engine.branding, invoicing=engine.invoicing
    )  # P2 invoices
    if format == "html":
        return _html(sheet)
    return PriceSheetOut.model_validate(service.as_dict(sheet))
