"""Scraper configuration — loaded from environment variables."""

from pydantic_settings import BaseSettings


class ScraperSettings(BaseSettings):
    """All scraper config via env vars, prefixed SCRAPER_."""

    port: int = 8001
    data_dir: str = "/data"
    db_path: str = "/data/scraper.db"
    concurrency: int = 5
    httpx_timeout: int = 30
    playwright_timeout: int = 60
    delivery_rate: int = 2
    min_word_count: int = 50

    # Where the RAG engine lives. Callers are authenticated against it
    # (GET /api/v1/whoami) and scraped pages are only ever delivered to its
    # /api/v1/ingest — never to a URL taken from a request.
    rag_url: str = "http://app:8000"
    # Browser origins allowed to call the JSON API: the dashboard (:8090), the legacy
    # dashboard (:8091, this release only) and the dashboard's Vite dev server (:5173).
    # From the environment this is a JSON list: SCRAPER_CORS_ORIGINS='["https://..."]'.
    cors_origins: list[str] = [
        "http://127.0.0.1:8090",
        "http://localhost:8090",
        "http://127.0.0.1:8091",
        "http://localhost:8091",
        "http://127.0.0.1:5173",
    ]
    # How long a key the engine accepted is trusted without asking it again.
    auth_cache_seconds: int = 60
    # The crawler refuses loopback, private, link-local and other non-public
    # addresses and internal hostnames (SSRF guard). True lifts that, for crawling
    # an intranet on purpose; http/https-only still applies.
    allow_private_networks: bool = False
    # Largest response body the crawler reads (page, sitemap, or any request the
    # headless browser makes); bigger ones are refused.
    max_response_bytes: int = 10 * 1024 * 1024

    # BIZ1: shared secret for POST /api/v1/render, the one route the engine's website
    # learning calls (it has no person's key). Empty = the route does not exist (404).
    render_token: str = ""

    model_config = {"env_prefix": "SCRAPER_"}

    @property
    def ingest_url(self) -> str:
        """The engine's ingest endpoint — the only place pages are delivered."""
        return self.rag_url.rstrip("/") + "/api/v1/ingest"


def get_settings() -> ScraperSettings:
    return ScraperSettings()
