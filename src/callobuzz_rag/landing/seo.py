"""robots.txt, sitemap.xml, structured data and the Open Graph address (LP1).

Every address is built from the configured base (`landing_sites.address`, else `RAG_PUBLIC_URL`),
never from the request: a forged `Host` header cannot reach any of this output. Structured data
claims only what is true of the page: an organisation and a software application, no offers and
no ratings; a FAQ block only where the deployer switched it on.
"""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from callobuzz_rag.landing.environment import SiteEnvironment
from callobuzz_rag.landing.render import HREFLANG, PAGE_PATHS, page_path
from callobuzz_rag.landing.schema import FaqSection, LandingContent


def robots_txt(base: str | None) -> str:
    lines = [
        "User-agent: *",
        "Disallow: /api/",
        "Disallow: /app",
        "Disallow: /_landing/preview/",
    ]
    if base:
        lines.append(f"Sitemap: {base.rstrip('/')}/sitemap.xml")
    return "\n".join(lines) + "\n"


def _enabled_pages(content: LandingContent) -> list[str]:
    return [page for page in PAGE_PATHS if page == "home" or bool(getattr(content, page).enabled)]


def sitemap_xml(
    content: LandingContent,
    base: str,
    published_at: datetime,
    legal_slugs: list[str],
) -> str:
    """Every enabled page in every language with its alternates, plus the legal pages."""
    root = base.rstrip("/")
    lastmod = published_at.date().isoformat()
    rows: list[str] = []
    for page in _enabled_pages(content):
        alternates = "".join(
            f'<xhtml:link rel="alternate" hreflang="{HREFLANG[lang]}" '
            f'href="{escape(root + page_path(page, lang))}"/>'
            for lang in content.languages
        )
        for lang in content.languages:
            rows.append(
                f"<url><loc>{escape(root + page_path(page, lang))}</loc>"
                f"<lastmod>{lastmod}</lastmod>{alternates}</url>"
            )
    for slug in legal_slugs:
        rows.append(
            f"<url><loc>{escape(f'{root}/legal/{slug}')}</loc><lastmod>{lastmod}</lastmod></url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + "\n".join(rows) + "\n</urlset>\n"
    )


def _dump(data: dict[str, Any]) -> str:
    """JSON for a `<script>` block: valid JSON with every `<` escaped, so no text can close the
    tag or open another one."""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def _text(value: Mapping[Any, str], lang: str, default: str, product: str) -> str:
    return (value.get(lang) or value.get(default) or "").replace("{product}", product)


def jsonld_blocks(content: LandingContent, env: SiteEnvironment, page: str, lang: str) -> list[str]:
    """Organization and SoftwareApplication on the home page, FAQPage where switched on."""
    if page != "home" or not env.address:
        return []
    base = env.address.rstrip("/")
    product = env.branding.product_name
    organization: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": product,
        "url": base + "/",
    }
    logo = env.branding.logo_url
    if logo:
        organization["logo"] = logo
    email = env.branding.support_email or env.company.email
    phone = env.company.phone
    if email or phone:
        point: dict[str, Any] = {"@type": "ContactPoint", "contactType": "customer support"}
        if email:
            point["email"] = str(email)
        if phone:
            point["telephone"] = phone
        organization["contactPoint"] = point
    blocks = [
        _dump(organization),
        _dump(
            {
                "@context": "https://schema.org",
                "@type": "SoftwareApplication",
                "name": product,
                "applicationCategory": "BusinessApplication",
                "operatingSystem": "Web",
            }
        ),
    ]
    default = content.default_language
    for section in content.home:
        if isinstance(section, FaqSection) and section.enabled and section.structured_data:
            blocks.append(
                _dump(
                    {
                        "@context": "https://schema.org",
                        "@type": "FAQPage",
                        "mainEntity": [
                            {
                                "@type": "Question",
                                "name": _text(item.q, lang, default, product),
                                "acceptedAnswer": {
                                    "@type": "Answer",
                                    "text": _text(item.a, lang, default, product),
                                },
                            }
                            for item in section.items
                        ],
                    }
                )
            )
    return blocks


def og_path(version: int, lang: str) -> str:
    return f"/_landing/og/{version}-{lang}.png"


def og_image_url(env: SiteEnvironment, version: int, lang: str) -> str | None:
    return f"{env.address.rstrip('/')}{og_path(version, lang)}" if env.address else None
