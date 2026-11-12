"""The public HTML page for a published legal page: one small self-contained document."""

from functools import cache
from typing import Final

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from callobuzz_rag.legal.service import format_date
from callobuzz_rag.legal.store import PublishedPage

# No script, no frames, nothing fetched from elsewhere but https images.
CSP: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


@cache
def _env() -> Environment:
    return Environment(
        loader=PackageLoader("callobuzz_rag.legal", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )


def render_page(
    page: PublishedPage, *, product_name: str, accent: str, pages: list[PublishedPage]
) -> str:
    """`page.html` for `page`. The frozen HTML is inserted as it is (it was made by
    `render.to_html` at publish); everything else is autoescaped."""
    return (
        _env()
        .get_template("page.html")
        .render(
            title=page.title,
            product_name=product_name,
            accent=accent,
            version=page.version,
            updated=format_date(page.published_at.date()),
            body_html=page.html,
            pages=pages,
            slug=page.slug,
        )
    )
