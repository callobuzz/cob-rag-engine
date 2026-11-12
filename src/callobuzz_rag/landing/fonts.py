"""Six self-hosted font pairings, each with a Devanagari fallback (LP1).

Every family is SIL OFL, shipped as WOFF2 by the front-end build (Fontsource) and served from
the engine's own address: no request leaves for a font service. The build writes
`fonts/fonts.json`, a manifest `{family key: {subset: file}}` relative to the build directory;
this module turns a pairing plus that manifest into `@font-face` rules, a font stack and at
most two preload links. Latin is loaded at once (`latin-ext`, which holds the rupee sign, only when a page uses it),
Devanagari by `unicode-range` only when a page
actually contains Hindi text.
"""

from dataclasses import dataclass
from typing import Final

FontManifest = dict[str, dict[str, str]]

LATIN_RANGE: Final = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,"
    "U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"
)
# Fontsource's `latin-ext`: it holds the rupee sign (U+20B9), so a price is set in the brand face.
LATIN_EXT_RANGE: Final = (
    "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,"
    "U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,"
    "U+A720-A7FF"
)
DEVANAGARI_RANGE: Final = (
    "U+0900-097F,U+1CD0-1CF9,U+200C-200D,U+20A8,U+25CC,U+A830-A839,U+A8E0-A8FF"
)


@dataclass(frozen=True)
class Family:
    key: str  # file key in the manifest
    css_name: str
    weights: str  # the `font-weight` descriptor: a range for variable fonts
    kind: str  # "sans" | "serif" | "mono"
    # Metric-matched fallback so the headline does not jump when the font arrives.
    fallback_local: str
    size_adjust: str
    ascent: str
    descent: str
    line_gap: str


def _family(
    key: str,
    css_name: str,
    weights: str,
    kind: str,
    adjust: str,
    ascent: str,
    descent: str,
    gap: str = "0%",
) -> Family:
    local = {"sans": "Arial", "serif": "Times New Roman", "mono": "Courier New"}[kind]
    return Family(key, css_name, weights, kind, local, adjust, ascent, descent, gap)


FAMILIES: Final[dict[str, Family]] = {
    f.key: f
    for f in (
        _family("inter-tight", "Inter Tight", "100 900", "sans", "97%", "98%", "24%"),
        _family("inter", "Inter", "100 900", "sans", "107%", "90%", "22%"),
        _family("geist", "Geist", "100 900", "sans", "104%", "93%", "25%"),
        _family("geist-mono", "Geist Mono", "100 900", "mono", "100%", "92%", "27%"),
        _family("instrument-serif", "Instrument Serif", "400", "serif", "83%", "108%", "30%"),
        _family("fraunces", "Fraunces", "100 900", "serif", "104%", "88%", "23%"),
        _family("manrope", "Manrope", "200 800", "sans", "105%", "100%", "28%"),
        _family(
            "bricolage-grotesque", "Bricolage Grotesque", "200 800", "sans", "103%", "94%", "25%"
        ),
        _family("dm-sans", "DM Sans", "100 1000", "sans", "104%", "92%", "29%"),
        _family("schibsted-grotesk", "Schibsted Grotesk", "400 900", "sans", "104%", "94%", "24%"),
        _family("red-hat-text", "Red Hat Text", "300 700", "sans", "105%", "93%", "25%"),
        _family(
            "noto-sans-devanagari", "Noto Sans Devanagari", "100 900", "sans", "100%", "100%", "50%"
        ),
        _family(
            "noto-serif-devanagari",
            "Noto Serif Devanagari",
            "100 900",
            "serif",
            "100%",
            "100%",
            "50%",
        ),
        _family("mukta", "Mukta", "400", "sans", "100%", "100%", "40%"),
        _family("hind", "Hind", "400", "sans", "100%", "100%", "40%"),
    )
}


@dataclass(frozen=True)
class Pairing:
    key: str
    label: str
    display: str
    body: str
    devanagari: str
    display_weight: int
    body_weight: int
    tracking: str
    mono: str | None = None


PAIRINGS: Final[dict[str, Pairing]] = {
    p.key: p
    for p in (
        Pairing(
            "neutral",
            "Neutral",
            "inter-tight",
            "inter",
            "noto-sans-devanagari",
            600,
            400,
            "-0.03em",
        ),
        Pairing(
            "engineered",
            "Engineered",
            "geist",
            "geist",
            "noto-sans-devanagari",
            500,
            400,
            "-0.035em",
            mono="geist-mono",
        ),
        Pairing(
            "editorial",
            "Editorial",
            "instrument-serif",
            "inter",
            "noto-serif-devanagari",
            400,
            400,
            "-0.02em",
        ),
        Pairing("warm", "Warm", "fraunces", "manrope", "mukta", 600, 400, "-0.02em"),
        Pairing("bold", "Bold", "bricolage-grotesque", "dm-sans", "hind", 700, 400, "-0.03em"),
        Pairing(
            "house",
            "House",
            "schibsted-grotesk",
            "red-hat-text",
            "noto-sans-devanagari",
            700,
            400,
            "-0.03em",
        ),
    )
}

_SYSTEM: Final = {
    "sans": "system-ui,-apple-system,'Segoe UI',Roboto,sans-serif",
    "serif": "Georgia,'Times New Roman',serif",
    "mono": "ui-monospace,SFMono-Regular,Menlo,monospace",
}


def pairing(key: str) -> Pairing:
    return PAIRINGS.get(key) or PAIRINGS["neutral"]


def _latin(manifest: FontManifest, key: str, weight: int) -> str | None:
    """The Latin file for a family at a weight: one variable file (`latin`) or the static file
    of that weight (`latin@700`)."""
    files = manifest.get(key, {})
    return files.get(f"latin@{weight}") or files.get("latin")


def _entries(files: dict[str, str]) -> list[tuple[str, str, str | None]]:
    """`(subset, file, weight)` for every file of a family; weight is None for a variable file."""
    out: list[tuple[str, str, str | None]] = []
    for name, path in sorted(files.items()):
        subset, _, weight = name.partition("@")
        out.append((subset, path, weight or None))
    return out


def families_of(p: Pairing) -> list[str]:
    """Distinct family keys of a pairing, display first."""
    keys = [p.display, p.body, p.devanagari]
    if p.mono:
        keys.append(p.mono)
    seen: list[str] = []
    for key in keys:
        if key not in seen:
            seen.append(key)
    return seen


def preloads(p: Pairing, manifest: FontManifest) -> list[str]:
    """Latin files to preload: the display face and the body face, at most two, when built."""
    files: list[str] = []
    for key, weight in ((p.display, p.display_weight), (p.body, p.body_weight)):
        path = _latin(manifest, key, weight)
        if path and path not in files:
            files.append(path)
    return files[:2]


def _face(family: Family, path: str, unicode_range: str, base_url: str, weight: str | None) -> str:
    return (
        "@font-face{"
        f'font-family:"{family.css_name}";font-style:normal;font-display:swap;'
        f"font-weight:{weight or family.weights};"
        f'src:url("{base_url}{path}") format("woff2");'
        f"unicode-range:{unicode_range}}}"
    )


# LP2 landing: a heading set in a bold weight must be measured against the system's BOLD face, or
# the headline wraps differently (and the page shifts) when the real font arrives. `size-adjust`
# values for the display families at their pairing weight, measured against Arial Bold / Times New
# Roman Bold (headline text at 40 px: real width / system width).
BOLD_FALLBACK: Final[dict[str, str]] = {
    "inter-tight": "93%",
    "fraunces": "112%",
    "bricolage-grotesque": "102%",
    "schibsted-grotesk": "101%",
}
_BOLD_LOCAL: Final = {
    "sans": 'local("Arial Bold"),local("Arial-BoldMT"),local("Liberation Sans Bold")',
    "serif": (
        'local("Times New Roman Bold"),local("TimesNewRomanPS-BoldMT"),'
        'local("Liberation Serif Bold")'
    ),
}
# /LP2 landing


def _fallback_face(family: Family, *, bold: bool = False) -> str:
    source = _BOLD_LOCAL.get(family.kind) if bold else None
    adjust = BOLD_FALLBACK.get(family.key) if bold and source else None
    return (
        "@font-face{"
        f'font-family:"{family.css_name} Fallback";'
        f"src:{source or f'local("{family.fallback_local}")'};"
        # the bold face answers every weight, so the browser never fakes a bolder one
        f"{'font-weight:100 900;' if adjust else ''}"
        f"size-adjust:{adjust or family.size_adjust};ascent-override:{family.ascent};"
        f"descent-override:{family.descent};line-gap-override:{family.line_gap}}}"
    )


def font_stack(key: str) -> str:
    family = FAMILIES[key]
    return f'"{family.css_name}","{family.css_name} Fallback"'


def font_face_css(p: Pairing, manifest: FontManifest, base_url: str = "/_landing/build/") -> str:
    """`@font-face` rules for the families of a pairing that the build produced, plus the
    `--font-display`, `--font-body` and `--font-mono` variables."""
    parts: list[str] = []
    for key in families_of(p):
        family = FAMILIES[key]
        for subset, path, weight in _entries(manifest.get(key, {})):
            if subset == "latin":
                parts.append(_face(family, path, LATIN_RANGE, base_url, weight))
            elif subset == "latin-ext":
                parts.append(_face(family, path, LATIN_EXT_RANGE, base_url, weight))
            elif subset == "devanagari":
                parts.append(_face(family, path, DEVANAGARI_RANGE, base_url, weight))
        if key in (p.display, p.body, p.mono):
            parts.append(_fallback_face(family, bold=key == p.display and p.display_weight >= 600))
    dev = FAMILIES[p.devanagari]
    display_kind = FAMILIES[p.display].kind
    body_kind = FAMILIES[p.body].kind
    display = f'{font_stack(p.display)},"{dev.css_name}",{_SYSTEM[display_kind]}'
    body = f'{font_stack(p.body)},"{dev.css_name}",{_SYSTEM[body_kind]}'
    mono = f"{font_stack(p.mono)},{_SYSTEM['mono']}" if p.mono else _SYSTEM["mono"]
    parts.append(
        ":root{"
        f"--font-display:{display};--font-body:{body};--font-mono:{mono};"
        f"--display-weight:{p.display_weight};--body-weight:{p.body_weight};"
        f"--display-tracking:{p.tracking}"
        "}"
    )
    return "".join(parts)
