"""Turns a published (or draft) site into HTML (LP1).

Server-rendered Jinja2: autoescaped, `StrictUndefined`, no field of the content is ever marked
safe. The only values that bypass escaping are built here from validated data: the colour
variables (hex only), the font rules, the JSON-LD blocks (`json.dumps`, `</` escaped) and the
two short inline scripts, whose text is fixed so the Content-Security-Policy can name their hash.
Every URL a page emits (canonical, alternates, Open Graph) is built from the site's configured
address and never from the request.
"""

import functools
import re
from dataclasses import dataclass, field
from typing import Any, Final

from jinja2 import Environment, PackageLoader, StrictUndefined, pass_context, select_autoescape
from jinja2.runtime import Context
from markupsafe import Markup

from callobuzz_rag.landing import (  # LP2 landing: home_ui
    build,
    home_ui,
    pricing,  # LP3 landing
    ui,
)
from callobuzz_rag.landing.environment import SiteEnvironment
from callobuzz_rag.landing.fonts import font_face_css, pairing, preloads
from callobuzz_rag.landing.schema import (
    LANG_PREFIX,
    LandingContent,
    Section,
)
from callobuzz_rag.landing.ui import LANGUAGE_LABELS

# The 200-byte motion check (research §7): motion classes only where JS runs and the visitor has
# not asked for less motion. Without it every section is simply visible.
MOTION_SCRIPT: Final = (
    'if(matchMedia("(prefers-reduced-motion: no-preference)").matches)'
    'document.documentElement.classList.add("motion-ok")'
)
# On the home page only: a browser that holds a dashboard session goes to /app, unless the link
# says ?landing=1. Reads the same key the dashboard writes (`RT_KEY` in lib/auth/storage.ts).
REDIRECT_SCRIPT: Final = (
    "try{if(!/[?&]landing=1/.test(location.search)&&"
    '(localStorage.getItem("cob.rt")||sessionStorage.getItem("cob.rt")))'
    'location.replace("/app")}catch(e){}'
)

PAGE_PATHS: Final[dict[str, str]] = {
    "home": "",
    "pricing": "/pricing",
    "features": "/features",
    "about": "/about",
    "contact": "/contact",
}
PAGE_TEMPLATES: Final[dict[str, str]] = {
    "home": "pages/home.html",
    "pricing": "pages/pricing.html",
    "features": "pages/features.html",
    "about": "pages/about.html",
    "contact": "pages/contact.html",
}
HREFLANG: Final[dict[str, str]] = {"en": "en", "hi": "hi", "hi-Latn": "hi-Latn"}


def page_path(page: str, lang: str) -> str:
    """The path of a page in a language: `/`, `/pricing`, `/hi/`, `/hi/pricing`."""
    prefix = LANG_PREFIX[lang]
    tail = PAGE_PATHS[page]
    if not tail:
        return (prefix + "/") if prefix else "/"
    return f"{prefix}{tail}"


@dataclass
class PageContext:
    content: LandingContent
    page: str
    lang: str
    theme_css: str
    env: SiteEnvironment
    version: int | None = None
    preview: bool = False
    jsonld: list[str] = field(default_factory=list)
    og_image: str | None = None
    media_token: str | None = None  # a preview proves itself to the media route with its token
    extra: dict[str, Any] = field(default_factory=dict)  # test and LP2/LP3 hooks


@dataclass(frozen=True)
class RenderedPage:
    html: str
    inline_scripts: list[str]


@pass_context
def _t(context: Context, value: Any) -> str:
    """One text for the page language (the default language where it is missing)."""
    if value is None:
        return ""
    product = context["env"].branding.product_name
    if isinstance(value, str):
        return value.replace("{product}", product)
    lang = context["lang"]
    default = context["c"].default_language
    text = value.get(lang) or value.get(default) or next(iter(value.values()), "")
    return str(text).replace("{product}", product)


_WHATSAPP_WORD = re.compile(r"whatsapp|व्हाट्स|वॉट्स|व्हॉट्स", re.IGNORECASE)


def _mentions_whatsapp(value: Any) -> bool:
    """True when a text names WhatsApp in Latin or Devanagari script (a Jinja test)."""
    return bool(_WHATSAPP_WORD.search(str(value or "")))


@functools.cache
def jinja_env() -> Environment:
    env = Environment(
        loader=PackageLoader("callobuzz_rag.landing", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["t"] = _t
    env.tests["mentions_whatsapp"] = _mentions_whatsapp
    return env


def _digits(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")


def whatsapp_link(phone: str | None) -> str | None:
    """`https://wa.me/<number>` from the company phone (a 10-digit Indian number gets 91)."""
    digits = _digits(phone)
    if len(digits) == 10:
        digits = "91" + digits
    return f"https://wa.me/{digits}" if 10 <= len(digits) <= 15 else None


def _alternates(ctx: PageContext, base: str) -> list[dict[str, str]]:
    out = [
        {"hreflang": HREFLANG[lang], "href": base + page_path(_page_key(ctx.page), lang)}
        for lang in ctx.content.languages
    ]
    if len(out) > 1:
        default = ctx.content.default_language
        out.append(
            {"hreflang": "x-default", "href": base + page_path(_page_key(ctx.page), default)}
        )
    return out


def _seo(ctx: PageContext) -> tuple[str, str]:
    product = ctx.env.branding.product_name
    page = ctx.page if ctx.page in PAGE_PATHS else "home"
    seo = getattr(ctx.content.seo, page)

    def pick(text: dict[str, str]) -> str:
        value = text.get(ctx.lang) or text.get(ctx.content.default_language) or ""
        return value.replace("{product}", product)

    title = pick(seo.title)
    return (f"{title} · {product}" if page != "home" else f"{title} | {product}"), pick(
        seo.description
    )


def _address_lines(env: SiteEnvironment) -> list[str]:
    return env.company.address_lines()


def render_page(ctx: PageContext, *, template: str | None = None) -> RenderedPage:
    """The full HTML of one page. `template` overrides the page's own (the 404 page)."""
    c, env = ctx.content, ctx.env
    base = (env.address or "").rstrip("/")
    lang = ctx.lang
    chrome = ui.strings(lang)
    page_key = ctx.page if ctx.page in PAGE_PATHS else "home"
    fonts = build.fonts()
    pair = pairing(c.theme.font_pairing)
    core = build.entry("core")
    home = build.entry("home" if page_key == "home" else "pages")
    css = list(dict.fromkeys([*core.css, *home.css]))
    js = list(dict.fromkeys([*core.js, *home.js]))
    title, description = _seo(ctx)

    def link(page: str) -> str:
        return page_path(page, lang)

    enabled_pages = {
        "pricing": c.pricing.enabled,
        "features": c.features.enabled,
        "about": c.about.enabled,
        "contact": c.contact.enabled,
    }

    def resolve(item: Any) -> dict[str, Any] | None:
        text = _t_plain(item.label, ctx)
        if item.page is not None:
            if item.page != "home" and not enabled_pages.get(item.page, True):
                return None
            href = link(item.page)
            return {"label": text, "href": href, "current": item.page == ctx.page}
        return {"label": text, "href": item.href, "current": False}

    nav_links = [r for r in (resolve(i) for i in c.nav.links) if r]
    footer_columns = []
    for col in c.footer.columns:
        links = [r for r in (resolve(i) for i in col.links) if r]
        if links:
            footer_columns.append({"title": _t_plain(col.title, ctx), "links": links})
    lang_links = [
        {
            "lang": HREFLANG[code],
            "label": LANGUAGE_LABELS[code],
            "href": page_path(ctx.page if ctx.page in PAGE_PATHS else "home", code),
            "current": code == lang,
        }
        for code in c.languages
    ]
    logo_name = env.media.get(str(c.theme.logo_asset_id)) if c.theme.logo_asset_id else None
    media_suffix = f"?t={ctx.media_token}" if ctx.media_token else ""
    logo_url = f"/_landing/media/{logo_name}{media_suffix}" if logo_name else env.branding.logo_url
    wa = whatsapp_link(env.company.phone) if env.whatsapp_on_sale else None
    redirect = (
        Markup(REDIRECT_SCRIPT)
        if ctx.page == "home" and lang == c.default_language and not ctx.preview
        else None
    )
    # LP2 landing: the WhatsApp chapter stays in the list; its template picks the WhatsApp or the
    # e-mail variant, so a deployer who does not sell the channel still tells the same story.
    sections = [s for s in c.home if s.enabled]
    inline = [MOTION_SCRIPT] + ([REDIRECT_SCRIPT] if redirect else [])
    # LP3 landing
    pricing_view = pricing.view_for_page(
        c,
        env,
        lang,
        page_key,
        currency=ctx.extra.get("currency"),
        period=ctx.extra.get("period"),
    )
    calc_data = pricing.calc_json(pricing_view)

    def teaser_cards(section: Any) -> Any:
        return pricing.teaser_view(pricing_view, section.plan_ids) if pricing_view else ()

    # /LP3 landing
    values: dict[str, Any] = {
        "pricing_view": pricing_view,  # LP3 landing
        "pricing_json": Markup(calc_data) if calc_data else None,  # LP3 landing
        "pricing_teaser_cards": teaser_cards,  # LP3 landing
        "c": c,
        "env": env,
        "ui": {**chrome, **home_ui.strings(lang)},
        "lang": lang,
        "page": ctx.page,
        "preview": ctx.preview,
        "noindex": ctx.preview,
        "title": title,
        "description": description,
        "canonical": base + page_path(page_key, lang),
        "alternates": _alternates(ctx, base) if base else [],
        "og_image": ctx.og_image,
        "theme_css": Markup(ctx.theme_css),
        "font_css": Markup(font_face_css(pair, fonts)),
        "preloads": [build.URL_PREFIX + p for p in preloads(pair, fonts)],
        "css": css,
        "js": js,
        "jsonld": [Markup(block) for block in ctx.jsonld],
        "motion_script": Markup(MOTION_SCRIPT),
        "redirect_script": redirect,
        "home_link": link("home"),
        "link": link,
        "nav_links": nav_links,
        "footer_columns": footer_columns,
        "lang_links": lang_links,
        "logo_url": logo_url,
        "whatsapp_link": wa,
        "phone_digits": "+" + _digits(env.company.phone) if env.company.phone else "",
        "contact_email": env.branding.support_email or env.company.email,
        "address_lines": _address_lines(env),
        "ask_ready": bool(env.ask_public_key and c.ask.enabled),
        "mobile_bar": ctx.page == "home",
        "sections": sections,
        "media": env.media,
        "media_suffix": media_suffix,
        **ctx.extra,
    }
    html = jinja_env().get_template(template or PAGE_TEMPLATES[page_key]).render(values)
    return RenderedPage(html=html, inline_scripts=inline)


def _t_plain(value: Any, ctx: PageContext) -> str:
    product = ctx.env.branding.product_name
    text = value.get(ctx.lang) or value.get(ctx.content.default_language) or ""
    return str(text).replace("{product}", product)


def render_expired(lang: str = "en") -> str:
    return (
        jinja_env()
        .get_template("pages/preview_expired.html")
        .render(lang=lang, ui=ui.strings(lang))
    )


__all__ = [
    "MOTION_SCRIPT",
    "REDIRECT_SCRIPT",
    "PageContext",
    "RenderedPage",
    "Section",
    "page_path",
    "render_expired",
    "render_page",
    "whatsapp_link",
]


def _page_key(page: str) -> str:
    return page if page in PAGE_PATHS else "home"
