"""The Open Graph image of a published version (LP1).

1200 x 630 PNG drawn at publish time from the theme: the page background, a soft glow of the
brand colour, the product name and the share headline in the bundled Noto Sans Bold (the same
file the invoices use, read only). A headline that is not Latin script falls back to the product
name alone, because that font has no Devanagari. Deterministic: the same inputs give the same
bytes. Pillow is imported inside the function (heavy import, deferred).
"""

import io
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from callobuzz_rag.landing.theme import ThemeTokens

if TYPE_CHECKING:
    from PIL import ImageFont

WIDTH: Final = 1200
HEIGHT: Final = 630
FONT: Final = Path(__file__).parents[1] / "billing" / "invoicing" / "fonts" / "NotoSans-Bold.ttf"
_EXTRA_LATIN: Final = frozenset(
    map(chr, (0x20B9, 0x2014, 0x2013, 0x2019, 0x2018, 0x201C, 0x201D, 0x2026, 0x2022, 0x00B7))
)


def is_latin(text: str) -> bool:
    return all(ord(ch) <= 0x24F or ch in _EXTRA_LATIN for ch in text)


def _rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _wrap(text: str, font: "ImageFont.FreeTypeFont", width: int, lines: int) -> list[str]:
    words = text.split()
    out: list[str] = []
    current = ""
    for index, word in enumerate(words):
        trial = f"{current} {word}".strip()
        if font.getlength(trial) <= width or not current:
            current = trial
            continue
        out.append(current)
        current = word
        if len(out) == lines - 1:
            current = " ".join(words[index:])
            break
    out.append(current)
    last = out[-1]
    if font.getlength(last) > width:  # the last line is too long: cut it with an ellipsis
        while last and font.getlength(last + "…") > width:
            last = last[:-1]
        out[-1] = last.rstrip() + "…"
    return out[:lines]


def render_og(tokens: ThemeTokens, product_name: str, headline: str, lang: str) -> bytes:
    """The PNG bytes. `lang` is part of the cache key elsewhere; the drawing depends on the
    text only."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    light = tokens.light
    image = Image.new("RGB", (WIDTH, HEIGHT), _rgb(light["bg"]))
    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw_glow = ImageDraw.Draw(glow)
    draw_glow.ellipse((620, -260, 1420, 440), fill=(*_rgb(light["glow-a"]), 140))
    draw_glow.ellipse((-260, 330, 520, 960), fill=(*_rgb(light["glow-b"]), 90))
    glow = glow.filter(ImageFilter.GaussianBlur(110))
    image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(image)

    ink = _rgb(light["ink"])
    margin = 80
    name_font: Any = ImageFont.truetype(str(FONT), 40)
    draw.text((margin, margin), product_name[:60], font=name_font, fill=_rgb(light["brand-text"]))
    if headline and is_latin(headline):
        font: Any = ImageFont.truetype(str(FONT), 76)
        lines = _wrap(headline, font, WIDTH - 2 * margin, 2)
        y = 250
        for line in lines:
            draw.text((margin, y), line, font=font, fill=ink)
            y += 98
    else:
        big: Any = ImageFont.truetype(str(FONT), 96)
        draw.text((margin, 260), product_name[:30], font=big, fill=ink)
    out = io.BytesIO()
    image.save(out, "PNG", compress_level=6)
    return out.getvalue()


def og_key(version: int, lang: str) -> str:
    return f"landing/og/{version}-{lang}.png"


async def ensure_image(
    session: Any,
    settings: Any,
    *,
    version: Any,
    lang: str,
    product_name: str,
) -> Any | None:
    """The stored share image of a published version in one language, drawn on first use.

    Drawn when a crawler first asks, not while publishing: a publish never waits for (or fails
    on) an image, and the image is the same whenever it is drawn because it depends only on the
    frozen version. Returns the asset row, or None when the language is not part of the version
    or storage is unavailable. The caller commits.
    """
    import asyncio
    import logging

    from sqlalchemy import select

    from callobuzz_rag.api.errors import APIError
    from callobuzz_rag.db.models.landing import LandingAsset
    from callobuzz_rag.landing import assets
    from callobuzz_rag.landing.schema import LandingContent

    key = og_key(version.version, lang)
    found = (
        await session.execute(select(LandingAsset).where(LandingAsset.storage_key == key))
    ).scalar_one_or_none()
    if found is not None:
        return found
    content = LandingContent.model_validate(version.content)
    if lang not in content.languages:
        return None
    text = content.seo.og_headline
    headline = (text.get(lang) or text.get(content.default_language) or "").replace(
        "{product}", product_name
    )
    tokens = ThemeTokens.from_json(version.theme)
    try:
        data = await asyncio.to_thread(render_og, tokens, product_name, headline, lang)
        return await assets.put_asset(
            session,
            settings,
            kind="og",
            content_type="image/png",
            data=data,
            created_by="system",
            size=(WIDTH, HEIGHT),
            key=key,
            upload_first=True,
        )
    except (APIError, OSError):
        logging.getLogger(__name__).warning(
            "could not make the share image for version %s (%s)",
            version.version,
            lang,
            exc_info=True,
        )
        return None
