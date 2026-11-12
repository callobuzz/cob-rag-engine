"""What a page needs from the rest of the engine, read once every few seconds (LP1).

Branding, company details, the legal pages, whether WhatsApp is on sale, the price list, the
uploaded files and whether "Ask it yourself" is ready. A page request never queries these one
by one: `EnvironmentCache` holds the last answer for `TTL_S` seconds, like the published site.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from callobuzz_rag.accounts.settings_store import (
    Branding,
    get_engine_settings,
    powered_text_for,
)
from callobuzz_rag.core.company import CompanyDetails
from callobuzz_rag.landing import service, store
from callobuzz_rag.landing.channels import whatsapp_on_sale
from callobuzz_rag.landing.pricing import PricingData, load_data  # LP3 landing

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

TTL_S: Final = 10.0


@dataclass(frozen=True)
class PlanView:
    """One plan as a visitor reads it: only words and amounts a customer could see."""

    name: str
    description: str
    price: str
    price_incl: str | None
    credit: str | None
    usage: str
    limits: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SiteEnvironment:
    branding: Branding
    company: CompanyDetails
    powered_text: str | None
    signup_available: bool
    legal_links: list[tuple[str, str]]  # (slug, title)
    whatsapp_on_sale: bool
    ask_public_key: str | None  # set only when "Ask it yourself" is ready
    billing_on: bool
    plans: list[PlanView]
    media: dict[str, str]  # asset id -> file name under /_landing/media/
    address: str | None  # the canonical base, never taken from a request
    pricing: PricingData | None = None  # LP3 landing: the pricing page's data


def signup_possible(settings: "Settings", signup_mode: str) -> bool:
    from callobuzz_rag.accounts.email.factory import email_configured
    from callobuzz_rag.accounts.tokens import accounts_enabled

    return (
        signup_mode != "closed"
        and accounts_enabled(settings)
        and email_configured(settings)
        and not settings.demo_mode
    )


async def _plans(session: Any, engine: Any) -> list[PlanView]:
    from callobuzz_rag.billing.price_sheet import add_inclusive_totals, build_sheet

    sheet = await build_sheet(session, engine.trust.price_sheet, engine.branding)
    sheet = add_inclusive_totals(sheet, engine.invoicing)
    return [
        PlanView(
            name=p.name,
            description=p.description,
            price=p.price,
            price_incl=p.price_incl,
            credit=p.credit,
            usage=p.usage,
            limits=list(p.limits),
        )
        for p in sheet.plans
    ]


async def load_environment(
    session_factory: Callable[[], Any],
    settings: "Settings",
    *,
    site_address: str | None,
    ask_widget_key_id: UUID | None,
) -> SiteEnvironment:
    from callobuzz_rag.billing.config import billing_enabled
    from callobuzz_rag.legal import store as legal_store

    engine = await get_engine_settings(session_factory)
    address = service.effective_address(site_address, settings.public_url)
    async with session_factory() as session:
        pages = await legal_store.published_all(session)
        on_sale = await whatsapp_on_sale(session)
        assets = await store.list_assets(session)

        status, key = await service.ask_key(session, ask_widget_key_id, service.origin_of(address))
        ask_key = key.public_key if status == "ready" and key is not None else None
        billing_on = billing_enabled()
        plans = await _plans(session, engine) if billing_on else []
        pricing_data = await load_data(session, engine, billing_on=billing_on)  # LP3 landing
    return SiteEnvironment(
        branding=engine.branding,
        company=engine.company,
        powered_text=powered_text_for(engine.branding),
        signup_available=signup_possible(settings, engine.signup_mode),
        legal_links=[(p.slug, p.title) for p in pages],
        whatsapp_on_sale=on_sale,
        ask_public_key=ask_key,
        billing_on=billing_on,
        plans=plans,
        media={str(a.id): store.media_name(a) for a in assets},
        address=address,
        pricing=pricing_data,  # LP3 landing
    )


class EnvironmentCache:
    def __init__(self, ttl: float = TTL_S) -> None:
        self._ttl = ttl
        self._value: SiteEnvironment | None = None
        self._key: tuple[str | None, UUID | None] | None = None
        self._at = 0.0
        self._lock: asyncio.Lock | None = None

    def invalidate(self) -> None:
        self._value = None

    async def get(
        self,
        session_factory: Callable[[], Any],
        settings: "Settings",
        *,
        site_address: str | None,
        ask_widget_key_id: UUID | None,
        now: float | None = None,
    ) -> SiteEnvironment:
        moment = time.monotonic() if now is None else now
        key = (site_address, ask_widget_key_id)
        value = self._value
        if value is not None and key == self._key and moment - self._at < self._ttl:
            return value
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._value is not None and key == self._key and moment - self._at < self._ttl:
                return self._value
            fresh = await load_environment(
                session_factory,
                settings,
                site_address=site_address,
                ask_widget_key_id=ask_widget_key_id,
            )
            self._value, self._key, self._at = fresh, key, moment
            return fresh


ENVIRONMENT = EnvironmentCache()
