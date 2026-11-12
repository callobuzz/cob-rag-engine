"""FastAPI application factory for the scraper service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from scraper.api.auth import EngineError, IdentityCache, engine_error_handler
from scraper.api.routes import router
from scraper.config import ScraperSettings, get_settings
from scraper.storage.database import Database


def create_app(
    db: Database | None = None,
    data_dir: str | None = None,
    settings: ScraperSettings | None = None,
) -> FastAPI:
    """Build and return the FastAPI application.

    Accepts optional db/data_dir/settings for testing.
    In production these are created from environment config.
    """
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Initialize DB on startup, close on shutdown."""
        if app.state.db is None:
            app.state.db = Database(resolved_settings.db_path)
            await app.state.db.initialize()

        yield

        if app.state.db is not None:
            await app.state.db.close()

    app = FastAPI(
        title="Callobuzz Web Scraper",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Attach state — injected values take priority over config defaults
    app.state.db = db
    app.state.data_dir = data_dir or resolved_settings.data_dir
    app.state.settings = resolved_settings
    app.state.identity_cache = IdentityCache(resolved_settings.auth_cache_seconds)
    app.state.background_tasks = set()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        # Authorization: dashboard sign-ins (Bearer), accepted since dashboard D8.
        allow_headers=["Content-Type", "X-API-Key", "Authorization"],
    )
    app.add_exception_handler(EngineError, engine_error_handler)

    app.include_router(router)

    # BIZ1 render
    from scraper.api.render import router as render_router

    app.include_router(render_router)
    # /BIZ1 render

    from scraper.dashboard.views import router as dashboard_router
    app.include_router(dashboard_router)

    return app
