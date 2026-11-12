"""A readable palette from one brand colour (LP1).

The deployer picks a brand colour (and optionally an accent). Whatever they pick, every text and
button pair this module produces has a measured WCAG contrast of at least 4.5:1 in both the light
and the dark set. The exact colour is kept for decoration (glow, rules); the button and the
coloured text move along the lightness axis only as far as needed, and `adjusted` says so in
plain words. One implementation, in Python: the dashboard previews through the API, so there is
no second copy to drift (decision D4).

Colour maths is OKLab / OKLCH (Bjorn Ottosson's matrices); contrast is WCAG 2.x relative
luminance. Everything is deterministic and pure.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

MIN_RATIO: Final = 4.5

# ------------------------------------------------------------------ colour maths


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"not a 6-digit hex colour: {value!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _from_linear(value: float) -> float:
    return 12.92 * value if value <= 0.0031308 else 1.055 * (value ** (1 / 2.4)) - 0.055


def luminance(value: str) -> float:
    r, g, b = (_to_linear(c / 255) for c in _hex_to_rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg_hex: str, bg_hex: str) -> float:
    """WCAG 2.x contrast ratio, 1.0 to 21.0."""
    a, b = luminance(fg_hex), luminance(bg_hex)
    hi, lo = (a, b) if a >= b else (b, a)
    return (hi + 0.05) / (lo + 0.05)


def hex_to_oklch(value: str) -> tuple[float, float, float]:
    """`(lightness 0..1, chroma, hue in degrees)`."""
    r, g, b = (_to_linear(c / 255) for c in _hex_to_rgb(value))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    chroma = math.hypot(a, bb)
    hue = math.degrees(math.atan2(bb, a)) % 360 if chroma > 1e-6 else 0.0
    return lightness, chroma, hue


def _oklch_to_linear(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def _in_gamut(rgb: tuple[float, float, float]) -> bool:
    return all(-0.0005 <= c <= 1.0005 for c in rgb)


def oklch_to_hex(lightness: float, chroma: float, hue: float) -> str:
    """Lower-case `#rrggbb`. A colour outside sRGB keeps its lightness and hue and loses chroma
    (found by bisection) rather than being clipped into another hue."""
    lightness = min(1.0, max(0.0, lightness))
    chroma = max(0.0, chroma)
    rgb = _oklch_to_linear(lightness, chroma, hue)
    if not _in_gamut(rgb):
        lo, hi = 0.0, chroma
        for _ in range(24):
            mid = (lo + hi) / 2
            if _in_gamut(_oklch_to_linear(lightness, mid, hue)):
                lo = mid
            else:
                hi = mid
        rgb = _oklch_to_linear(lightness, lo, hue)
    out = ""
    for channel in rgb:
        value = round(min(1.0, max(0.0, _from_linear(min(1.0, max(0.0, channel))))) * 255)
        out += f"{value:02x}"
    return "#" + out


# ------------------------------------------------------------------ the derivation


@dataclass(frozen=True)
class ContrastRow:
    pair: str
    theme: str
    fg: str
    bg: str
    ratio: float


@dataclass(frozen=True)
class ThemeTokens:
    light: dict[str, str]
    dark: dict[str, str]
    report: list[ContrastRow]
    adjusted: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "light": dict(self.light),
            "dark": dict(self.dark),
            "report": [
                {"pair": r.pair, "theme": r.theme, "fg": r.fg, "bg": r.bg, "ratio": r.ratio}
                for r in self.report
            ],
            "adjusted": list(self.adjusted),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ThemeTokens":
        return cls(
            light=dict(data["light"]),
            dark=dict(data["dark"]),
            report=[ContrastRow(**row) for row in data["report"]],
            adjusted=list(data["adjusted"]),
        )


_RAMP_L: Final = (0.98, 0.95, 0.90, 0.84, 0.76, 0.68, 0.60, 0.52, 0.44, 0.36, 0.28, 0.20)
_RAMP_C: Final = (0.12, 0.25, 0.45, 0.65, 0.85, 1.0, 1.0, 1.0, 0.9, 0.75, 0.55, 0.40)
_WHITE: Final = "#ffffff"


def _search(
    lightness: float,
    chroma: float,
    hue: float,
    passes: Callable[[str], bool],
    toward: float,
) -> str:
    """The colour closest to the start (same hue and chroma) that passes, moving lightness
    toward `toward` (0 = darker, 1 = lighter). The start itself when it already passes."""
    start = oklch_to_hex(lightness, chroma, hue)
    if passes(start):
        return start
    far = oklch_to_hex(toward, chroma, hue)
    if not passes(far):
        far = oklch_to_hex(toward, 0.0, hue)  # an achromatic extreme always passes
    lo, hi = lightness, toward
    best = far
    for _ in range(16):
        mid = (lo + hi) / 2
        candidate = oklch_to_hex(mid, chroma, hue)
        if passes(candidate):
            hi, best = mid, candidate
        else:
            lo = mid
    return best


def _readable_on(backgrounds: list[str]) -> Callable[[str], bool]:
    return lambda fg: all(contrast(fg, bg) >= MIN_RATIO for bg in backgrounds)


def derive_theme(brand_hex: str, accent_hex: str | None) -> ThemeTokens:
    """The full light and dark token sets for a brand colour. Pure and deterministic."""
    brand = brand_hex.lower()
    accent = accent_hex.lower() if accent_hex else None
    lightness, chroma, hue = hex_to_oklch(brand)
    adjusted: list[str] = []
    report: list[ContrastRow] = []

    def note(sentence: str) -> None:
        if sentence not in adjusted:
            adjusted.append(sentence)

    def measure(theme: str, pair: str, fg: str, bg: str) -> None:
        report.append(ContrastRow(pair, theme, fg, bg, round(contrast(fg, bg), 2)))

    def build(theme: str) -> dict[str, str]:
        dark = theme == "dark"
        toward_text = 1.0 if dark else 0.0
        if dark:
            bg = oklch_to_hex(0.16, 0.012, hue)
            bg2 = oklch_to_hex(0.19, 0.014, hue)
            surface = oklch_to_hex(0.22, 0.014, hue)
            ink = oklch_to_hex(0.96, 0.006, hue)
            rule = oklch_to_hex(0.30, 0.014, hue)
        else:
            bg = oklch_to_hex(0.985, 0.008, hue)
            bg2 = oklch_to_hex(0.965, 0.010, hue)
            surface = oklch_to_hex(0.997, 0.003, hue)
            ink = oklch_to_hex(0.20, 0.012, hue)
            rule = oklch_to_hex(0.90, 0.010, hue)
        grounds = [bg, bg2, surface]
        ink2 = _search(0.78 if dark else 0.42, 0.010, hue, _readable_on(grounds), toward_text)
        ink3 = _search(0.70 if dark else 0.50, 0.010, hue, _readable_on(grounds), toward_text)

        # The button. Light set: white label, the colour darkens until it holds the label.
        # Dark set: near-black label, the colour lightens until it holds the label.
        if dark:
            brand_ink = oklch_to_hex(0.15, 0.010, hue)
            button = _search(
                lightness, chroma, hue, lambda c: contrast(brand_ink, c) >= MIN_RATIO, 1.0
            )
            if button != brand:
                note(
                    "In dark mode, buttons use a lighter shade of your colour "
                    "so their text stays readable."
                )
        else:
            brand_ink = _WHITE
            button = _search(
                lightness, chroma, hue, lambda c: contrast(brand_ink, c) >= MIN_RATIO, 0.0
            )
            if button != brand:
                note("Buttons use a darker shade of your colour so their text stays readable.")

        soft = oklch_to_hex(0.27 if dark else 0.955, min(chroma, 0.05), hue)
        text_grounds = [bg, bg2, surface, soft]
        brand_text = _search(lightness, chroma, hue, _readable_on(text_grounds), toward_text)
        if brand_text != brand:
            note(
                "Links and highlighted words use a "
                f"{'lighter' if dark else 'darker'} shade of your colour "
                "so they stay readable on the page."
            )
        accent_decor = accent or brand
        if accent:
            a_l, a_c, a_h = hex_to_oklch(accent)
            accent_text = _search(a_l, a_c, a_h, _readable_on(grounds), toward_text)
            if accent_text != accent:
                note(
                    "Your accent colour is used as a "
                    f"{'lighter' if dark else 'darker'} shade where it is text."
                )
        else:
            accent_text = brand_text
        glow_b = accent or oklch_to_hex(0.80, min(chroma, 0.12), (hue + 40) % 360)

        tokens: dict[str, str] = {
            "bg": bg,
            "bg-2": bg2,
            "surface": surface,
            "ink": ink,
            "ink-2": ink2,
            "ink-3": ink3,
            "rule": rule,
            "brand": button,
            "brand-ink": brand_ink,
            "brand-text": brand_text,
            "brand-soft": soft,
            "accent": accent_decor,
            "accent-text": accent_text,
            "focus": brand_text,
            "glow-a": brand,
            "glow-b": glow_b,
            "scrim": f"{bg}d1",
        }
        ramp = [
            oklch_to_hex(level, chroma * taper, hue)
            for level, taper in zip(_RAMP_L, _RAMP_C, strict=True)
        ]
        if dark:
            ramp.reverse()
        for index, colour in enumerate(ramp, start=1):
            tokens[f"brand-{index}"] = colour

        for pair, fg, grounds_for_pair in (
            ("ink", ink, grounds),
            ("ink-2", ink2, grounds),
            ("ink-3", ink3, grounds),
            ("brand-text", brand_text, text_grounds),
            ("accent-text", accent_text, grounds),
        ):
            for ground in grounds_for_pair:
                measure(theme, f"{pair} on {_name_of(tokens, ground)}", fg, ground)
        measure(theme, "brand-ink on brand", brand_ink, button)
        return tokens

    light = build("light")
    dark = build("dark")
    return ThemeTokens(light=light, dark=dark, report=report, adjusted=adjusted)


def _name_of(tokens: dict[str, str], colour: str) -> str:
    for name in ("bg", "bg-2", "surface", "brand-soft"):
        if tokens[name] == colour:
            return name
    return colour


# ------------------------------------------------------------------ CSS


def _block(tokens: dict[str, str], scheme: str) -> str:
    body = ";".join(f"--{name}:{value}" for name, value in tokens.items())
    return f"color-scheme:{scheme};{body}"


def theme_css(tokens: ThemeTokens, base: str) -> str:
    """The colour variables as CSS, built only from validated hex values.

    `light` and `dark` set that palette on `:root` and offer the other under `data-theme`;
    `auto` follows the visitor's setting and still lets `data-theme` override it.
    """
    light = _block(tokens.light, "light")
    dark = _block(tokens.dark, "dark")
    if base == "dark":
        return f':root{{{dark}}}:root[data-theme="light"]{{{light}}}'
    if base == "light":
        return f':root{{{light}}}:root[data-theme="dark"]{{{dark}}}'
    return (
        f":root{{{light}}}"
        f'@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{{dark}}}}}'
        f':root[data-theme="dark"]{{{dark}}}'
        f':root[data-theme="light"]{{{light}}}'
    )
