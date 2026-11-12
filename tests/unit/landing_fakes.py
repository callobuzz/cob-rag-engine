"""Helpers for the public-website tests: a small app around the landing router, a fake live
site and a fake environment, so no test needs a database or the front-end build."""

import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from callobuzz_rag.accounts.settings_store import Branding
from callobuzz_rag.api.routes import landing as landing_routes
from callobuzz_rag.core.company import CompanyDetails
from callobuzz_rag.landing import snapshot, store
from callobuzz_rag.landing.cache import PublishedSite
from callobuzz_rag.landing.environment import PlanView, SiteEnvironment
from callobuzz_rag.landing.schema import LandingContent, default_content
from callobuzz_rag.landing.theme import theme_css
from tests.unit.accounts_fakes import FakeDB

ADDRESS = "https://www.sharmadental.in"
SECRET = "s" * 40


def make_env(**over: Any) -> SiteEnvironment:
    values: dict[str, Any] = {
        "branding": Branding(product_name="Sahayak", support_email="hello@sharmadental.in"),
        "company": CompanyDetails(
            legal_name="Sharma Dental Clinic",
            address_line1="12 MG Road",
            city="Pune",
            state_code="27",
            pincode="411001",
            phone="+91 98220 12345",
        ),
        "powered_text": "Powered by cob-rag-engine by Callobuzz",
        "signup_available": True,
        "legal_links": [("terms", "Terms of Service"), ("privacy", "Privacy Notice")],
        "whatsapp_on_sale": True,
        "ask_public_key": None,
        "billing_on": True,
        "plans": [
            PlanView(
                name="Standard",
                description="For a clinic getting started",
                price="₹999 a month",
                price_incl="₹1,178.82 a month incl. GST",
                credit="₹500 of credit included every month",
                usage="₹2 per question",
                limits=[("Knowledge bases", "3")],
            )
        ],
        "media": {},
        "address": ADDRESS,
        "pricing": snapshot.example_pricing_data(),  # LP3 landing
    }
    values.update(over)
    return SiteEnvironment(**values)


def content_with(mutate: Any = None, **top: Any) -> LandingContent:
    data = copy.deepcopy(default_content().model_dump(mode="json"))
    data.update(top)
    if mutate is not None:
        mutate(data)
    return LandingContent.model_validate(data)


def make_site(content: LandingContent | None = None, *, version: int = 3) -> PublishedSite:
    content = content or default_content()
    tokens = store.theme_of(content)
    return PublishedSite(
        version=version,
        content=content,
        theme=tokens,
        theme_css=theme_css(tokens, content.theme.base),
        content_hash=store.content_hash(content.model_dump(mode="json"), tokens.to_json()),
        published_at=datetime(2026, 10, 10, 9, 0, tzinfo=UTC),
        address=ADDRESS,
        ask_widget_key_id=None,
    )


def make_settings(**over: Any) -> Any:
    values = {
        "session_secret": SecretStr(SECRET),
        "dashboard_url": "http://127.0.0.1:8090",
        "public_url": "",
        "debug": False,
        "is_prod": False,
    }
    values.update(over)
    return SimpleNamespace(**values)


def make_client(
    monkeypatch: Any,
    *,
    site: PublishedSite | None,
    env: SiteEnvironment | None = None,
    db: FakeDB | None = None,
    settings: Any = None,
) -> httpx.AsyncClient:
    """A client for an app that has only the landing router."""
    app = FastAPI()
    app.include_router(landing_routes.router)
    database = db or FakeDB()
    app.state.session_factory = database.factory()
    app.state.settings = settings or make_settings()
    the_env = env or make_env()

    async def live(_request: Any) -> PublishedSite | None:
        return site

    async def environment(_request: Any, **_kw: Any) -> SiteEnvironment:
        return the_env

    monkeypatch.setattr(landing_routes, "get_live_site", live)
    monkeypatch.setattr(landing_routes, "get_environment", environment)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def make_admin_client(db: FakeDB, settings: Any = None) -> httpx.AsyncClient:
    """The editor API on an app that has only its router, with the operator check, the session
    and the settings replaced."""
    from collections.abc import AsyncIterator

    from callobuzz_rag.api import deps
    from callobuzz_rag.api.errors import register_error_handlers
    from callobuzz_rag.api.routes import landing_admin

    app = FastAPI()

    register_error_handlers(app)
    app.include_router(landing_admin.router)
    the_settings = settings or make_settings(
        landing_video_max_mb=25,
        landing_image_max_mb=5,
        landing_cache_dir="",
        s3_bucket="rag-documents",
        storage_provider="local",
        storage_local_root="unused",
    )
    app.state.settings = the_settings

    async def session() -> AsyncIterator[Any]:
        yield db.session()

    app.dependency_overrides[deps.require_admin] = lambda: None
    app.dependency_overrides[deps.get_session] = session
    app.dependency_overrides[deps.get_settings] = lambda: the_settings
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
