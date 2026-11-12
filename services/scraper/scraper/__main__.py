"""scraper/__main__.py — Run the scraper service."""

import uvicorn

from scraper.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "scraper.api.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
