"""LP1 T3: a readable palette for any brand colour, and the six font pairings."""

import itertools

import pytest

from callobuzz_rag.landing.fonts import (
    DEVANAGARI_RANGE,
    FAMILIES,
    PAIRINGS,
    families_of,
    font_face_css,
    preloads,
)
from callobuzz_rag.landing.theme import (
    ThemeTokens,
    contrast,
    derive_theme,
    hex_to_oklch,
    oklch_to_hex,
    theme_css,
)

HUES = range(0, 360, 15)
LIGHTNESS = (0.30, 0.45, 0.60, 0.72, 0.85, 0.95)
CHROMA = (0.02, 0.10, 0.18, 0.30)  # 0.30 is out of gamut for most hues: clamped, not a crash


def test_contrast_matches_wcag_reference_values() -> None:
    assert contrast("#ffffff", "#000000") == pytest.approx(21.0, abs=0.01)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert contrast("#2f8cff", "#2f8cff") == pytest.approx(1.0)


def test_oklch_round_trip_is_stable() -> None:
    for hex_ in ("#0f766e", "#e11d48", "#facc15", "#06b6d4", "#111111", "#fefefe"):
        assert oklch_to_hex(*hex_to_oklch(hex_)) == hex_


def test_out_of_gamut_colours_lose_chroma_not_hue() -> None:
    value = oklch_to_hex(0.6, 0.35, 140)
    _, chroma, hue = hex_to_oklch(value)
    assert chroma < 0.35 and abs(hue - 140) < 6


@pytest.mark.parametrize(("h", "lightness", "c"), list(itertools.product(HUES, LIGHTNESS, CHROMA)))
def test_every_text_pair_is_readable_for_any_brand_colour(
    h: int, lightness: float, c: float
) -> None:
    brand = oklch_to_hex(lightness, c, h)
    tokens = derive_theme(brand, None)
    assert tokens.report, "the report lists every text pair"
    for row in tokens.report:
        assert row.ratio >= 4.5, (brand, row)
        assert contrast(row.fg, row.bg) >= 4.5, (brand, row)
    for theme in (tokens.light, tokens.dark):
        assert contrast(theme["brand-ink"], theme["brand"]) >= 4.5  # label on the button
        assert contrast(theme["brand-text"], theme["bg"]) >= 4.5  # links, highlighted words
        assert contrast(theme["ink-2"], theme["surface"]) >= 4.5  # muted text on cards


def test_the_exact_brand_colour_is_kept_for_decoration() -> None:
    tokens = derive_theme("#facc15", None)  # yellow: white text fails, the button moves
    assert tokens.light["glow-a"] == "#facc15"
    assert tokens.light["brand"] != "#facc15"
    assert any("darker shade" in s for s in tokens.adjusted)


def test_a_colour_that_already_works_is_not_touched() -> None:
    tokens = derive_theme("#0f766e", None)
    assert tokens.light["brand"] == "#0f766e"
    assert not any("darker shade of your colour so their text" in s for s in tokens.adjusted)


def test_an_accent_gets_its_own_readable_text_colour() -> None:
    tokens = derive_theme("#0f766e", "#f97316")
    for theme in (tokens.light, tokens.dark):
        assert contrast(theme["accent-text"], theme["bg"]) >= 4.5
        assert theme["accent"] == "#f97316"


def test_the_ramp_has_twelve_steps_and_dark_is_reversed() -> None:
    tokens = derive_theme("#0f766e", None)
    steps = [f"brand-{i}" for i in range(1, 13)]
    assert all(s in tokens.light and s in tokens.dark for s in steps)
    assert tokens.light["brand-1"] == tokens.dark["brand-12"]
    assert tokens.light["brand-12"] == tokens.dark["brand-1"]


def test_derivation_is_deterministic_and_round_trips_through_json() -> None:
    first = derive_theme("#e11d48", "#06b6d4")
    assert first == derive_theme("#e11d48", "#06b6d4")
    assert ThemeTokens.from_json(first.to_json()) == first


def test_css_is_one_root_block_per_theme_and_escapes_nothing_user_typed() -> None:
    css = theme_css(derive_theme("#0f766e", None), "auto")
    assert css.count(":root") >= 2 and "prefers-color-scheme: dark" in css
    assert "<" not in css and "}" in css
    assert css.count("{") == css.count("}")


def test_base_theme_decides_what_sits_on_root() -> None:
    tokens = derive_theme("#0f766e", None)
    light = theme_css(tokens, "light")
    dark = theme_css(tokens, "dark")
    assert light.startswith(f":root{{color-scheme:light;--bg:{tokens.light['bg']}")
    assert dark.startswith(f":root{{color-scheme:dark;--bg:{tokens.dark['bg']}")
    assert 'data-theme="dark"' in light and "prefers-color-scheme" not in light
    assert 'data-theme="light"' in dark and "prefers-color-scheme" not in dark


# ---------------------------------------------------------------- fonts


def _manifest() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for key in FAMILIES:
        if "devanagari" in key or key in ("mukta", "hind"):
            out[key] = {"devanagari": f"fonts/{key}-devanagari.woff2"}
        else:
            out[key] = {"latin": f"fonts/{key}-latin.woff2"}
    return out


def test_there_are_six_pairings_each_with_a_devanagari_family() -> None:
    assert set(PAIRINGS) == {"neutral", "engineered", "editorial", "warm", "bold", "house"}
    manifest = _manifest()
    for pairing in PAIRINGS.values():
        assert "devanagari" in manifest[pairing.devanagari]
        latin = [manifest[k]["latin"] for k in (pairing.display, pairing.body)]
        assert len(latin) == 2 and all(path.startswith("fonts/") for path in latin)
        assert set(families_of(pairing)) <= set(FAMILIES)


def test_at_most_two_files_are_preloaded() -> None:
    for pairing in PAIRINGS.values():
        files = preloads(pairing, _manifest())
        assert 1 <= len(files) <= 2
        assert all("devanagari" not in f for f in files)


def test_devanagari_faces_load_by_unicode_range_only() -> None:
    css = font_face_css(PAIRINGS["neutral"], _manifest())
    assert f"unicode-range:{DEVANAGARI_RANGE}" in css
    assert css.count("font-display:swap") >= 3
    assert '"Inter Tight Fallback"' in css and "size-adjust" in css
    assert "--font-display:" in css and "--font-body:" in css


def test_a_family_the_build_did_not_produce_is_left_out() -> None:
    css = font_face_css(PAIRINGS["warm"], {})
    assert '@font-face{font-family:"Fraunces"' not in css
    assert "--font-display:" in css


def test_static_weights_get_their_own_faces_and_the_right_preloads() -> None:
    manifest = {
        "schibsted-grotesk": {
            "latin@400": "fonts/sg-400.woff2",
            "latin@700": "fonts/sg-700.woff2",
        },
        "red-hat-text": {"latin@400": "fonts/rh-400.woff2", "latin@600": "fonts/rh-600.woff2"},
        "noto-sans-devanagari": {"devanagari": "fonts/noto-dev.woff2"},
    }
    house = PAIRINGS["house"]
    assert preloads(house, manifest) == ["fonts/sg-700.woff2", "fonts/rh-400.woff2"]
    css = font_face_css(house, manifest)
    assert 'font-weight:700;src:url("/_landing/build/fonts/sg-700.woff2")' in css
    assert 'font-weight:400;src:url("/_landing/build/fonts/sg-400.woff2")' in css
    assert 'font-weight:600;src:url("/_landing/build/fonts/rh-600.woff2")' in css
    assert DEVANAGARI_RANGE in css and "fonts/noto-dev.woff2" in css


def test_a_pairing_with_nothing_built_still_gives_a_usable_font_stack() -> None:
    css = font_face_css(PAIRINGS["neutral"], {})
    assert '@font-face{font-family:"Inter Tight"' not in css
    assert "--font-body:" in css and "sans-serif" in css


# ------------------------------------------------ rupee sign and Latin ranges (review LP1)


def _covers(unicode_range: str, code_point: int) -> bool:
    for part in unicode_range.split(","):
        lo, _, hi = part.removeprefix("U+").partition("-")
        if int(lo, 16) <= code_point <= int(hi or lo, 16):
            return True
    return False


def _ranges_for(css: str, family_css_name: str) -> list[str]:
    faces = [f for f in css.split("@font-face{") if f'font-family:"{family_css_name}";' in f]
    return [f.split("unicode-range:")[1].split("}")[0] for f in faces if "unicode-range:" in f]


def test_latin_range_is_fontsources_exact_latin_range() -> None:
    from callobuzz_rag.landing.fonts import LATIN_RANGE

    assert LATIN_RANGE == (
        "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,"
        "U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"
    )
    assert _covers(LATIN_RANGE, 0x0304) and _covers(LATIN_RANGE, 0x0308)


def test_the_rupee_sign_resolves_to_the_body_face_not_the_devanagari_one() -> None:
    manifest = {
        key: (
            {"devanagari": f"fonts/{key}-dev.woff2"}
            if key in ("mukta", "hind") or "devanagari" in key
            else {"latin": f"fonts/{key}-l.woff2", "latin-ext": f"fonts/{key}-le.woff2"}
        )
        for key in FAMILIES
    }
    for pairing in PAIRINGS.values():
        css = font_face_css(pairing, manifest)
        body = FAMILIES[pairing.body].css_name
        assert any(_covers(r, 0x20B9) for r in _ranges_for(css, body)), pairing.key
        dev = FAMILIES[pairing.devanagari].css_name
        assert not any(_covers(r, 0x20B9) for r in _ranges_for(css, dev)), pairing.key


def test_latin_ext_is_never_preloaded() -> None:
    manifest = {
        "inter-tight": {"latin": "a.woff2", "latin-ext": "ax.woff2"},
        "inter": {"latin": "b.woff2", "latin-ext": "bx.woff2"},
    }
    assert preloads(PAIRINGS["neutral"], manifest) == ["a.woff2", "b.woff2"]


def test_every_pairing_of_the_committed_build_has_its_files() -> None:
    from callobuzz_rag.landing import build

    if not (build.BUILD_DIR / "fonts" / "fonts.json").is_file():
        pytest.skip("the landing build is absent")
    build.reload()
    manifest = build.fonts()
    for pairing in PAIRINGS.values():
        files = preloads(pairing, manifest)
        assert 1 <= len(files) <= 2, pairing.key
        assert all((build.BUILD_DIR / f).is_file() for f in files), pairing.key
        assert sum((build.BUILD_DIR / f).stat().st_size for f in files) <= 120 * 1024
        assert manifest.get(pairing.devanagari), pairing.key
        for key in (pairing.display, pairing.body):
            assert any(s.startswith("latin-ext") for s in manifest[key]), (pairing.key, key)


# LP2 landing: a bold headline is measured against the system's bold face
def test_bold_display_fonts_get_a_bold_fallback_face_for_every_weight() -> None:
    import re

    from callobuzz_rag.landing.fonts import font_face_css, pairing

    manifest: dict[str, dict[str, str]] = {}
    for key in ("neutral", "warm", "bold", "house", "engineered", "editorial"):
        css = font_face_css(pairing(key), manifest)
        faces = re.findall(r'@font-face\{font-family:"([^"]+) Fallback";([^}]*)\}', css)
        display = faces[0]
        if pairing(key).display_weight >= 600:
            assert "Bold" in display[1] and "font-weight:100 900" in display[1], key
        else:
            assert "Bold" not in display[1], key
    editorial = font_face_css(pairing("editorial"), manifest)
    assert "size-adjust:83%" in editorial  # measured, not guessed
