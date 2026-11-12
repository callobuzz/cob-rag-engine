"""Render the website to static files without a database (LP1, decision D23).

    python -m callobuzz_rag.landing.snapshot --content default --brand '#0f766e' --base light \\
        --lang en --out <dir>

Every enabled page in the chosen language (or `all`) is written as `index.html`,
`pricing/index.html`, ... with the committed front-end build copied to `_landing/build/`, so a
plain static server (the Playwright runs of LP2 and LP3) and the size-budget test can look at real
pages without starting the stack. The engine around the page is a fixed example: a dental clinic in
Pune, three plans from `tests/fixtures/landing/pricing_view.json`, WhatsApp on sale.
"""

import argparse
import json
import shutil
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from callobuzz_rag.accounts.settings_store import Branding
from callobuzz_rag.core.company import CompanyDetails
from callobuzz_rag.landing import build, render
from callobuzz_rag.landing.environment import PlanView, SiteEnvironment
from callobuzz_rag.landing.schema import LANG_PREFIX, LandingContent, default_content
from callobuzz_rag.landing.store import theme_of
from callobuzz_rag.landing.theme import theme_css

if TYPE_CHECKING:
    from callobuzz_rag.landing.pricing import PricingData  # LP3 landing

FIXTURE: Final = Path(__file__).parents[3] / "tests" / "fixtures" / "landing" / "pricing_view.json"
ADDRESS: Final = "https://www.sharmadental.in"


def load_content(spec: str) -> LandingContent:
    """`default`, `preset:<key>` (the starting examples of LP2) or the path of a JSON file."""
    if spec == "default":
        return default_content()
    if spec.startswith("preset:"):
        from callobuzz_rag.landing import presets

        try:
            return presets.preset_site(spec.removeprefix("preset:"))
        except presets.PresetNotFoundError as exc:
            raise SystemExit(f"there is no preset {spec.removeprefix('preset:')}") from exc
    return LandingContent.model_validate_json(Path(spec).read_text(encoding="utf-8"))


def fixture_plans(path: Path = FIXTURE) -> list[PlanView]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        PlanView(
            name=row["name"],
            description=row["description"],
            price=row["price"],
            price_incl=row["price_incl"],
            credit=row["credit"],
            usage=row["usage"],
            limits=[(label, value) for label, value in row["limits"]],
        )
        for row in rows
    ]


def example_environment(
    plans: list[PlanView] | None = None, *, ask_key: str | None = None, whatsapp: bool = True
) -> SiteEnvironment:
    return SiteEnvironment(
        branding=Branding(product_name="Sahayak", support_email="hello@sharmadental.in"),
        company=CompanyDetails(
            legal_name="Sharma Dental Clinic",
            address_line1="12 MG Road",
            city="Pune",
            state_code="27",
            pincode="411001",
            phone="+91 98220 12345",
        ),
        powered_text="Powered by cob-rag-engine by Callobuzz",
        signup_available=True,
        legal_links=[("terms", "Terms of Service"), ("privacy", "Privacy Notice")],
        whatsapp_on_sale=whatsapp,
        ask_public_key=ask_key,
        billing_on=True,
        plans=fixture_plans() if plans is None else plans,
        media={},
        address=ADDRESS,
        pricing=example_pricing_data(),  # LP3 landing
    )


def render_site(
    content: LandingContent,
    out: Path,
    *,
    languages: list[str],
    env: SiteEnvironment | None = None,
    pages: list[str] | None = None,
) -> list[Path]:
    """Write the pages; returns the files made."""
    env = env or example_environment()
    tokens = theme_of(content)
    css = theme_css(tokens, content.theme.base)
    written: list[Path] = []
    for lang in languages:
        prefix = LANG_PREFIX[lang].strip("/")
        for page in render.PAGE_PATHS:
            if pages is not None and page not in pages:
                continue
            if page != "home" and not getattr(content, page).enabled:
                continue
            ctx = render.PageContext(content=content, page=page, lang=lang, theme_css=css, env=env)
            html = render.render_page(ctx).html
            folder = out.joinpath(*(p for p in (prefix, "" if page == "home" else page) if p))
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "index.html"
            target.write_text(html, encoding="utf-8")
            written.append(target)
    if build.BUILD_DIR.is_dir():
        destination = out / "_landing" / "build"
        shutil.copytree(build.BUILD_DIR, destination, dirs_exist_ok=True)
    return written


def _backdrop_demo(data: dict[str, object], out: Path) -> dict[str, str]:
    """A hero background video for the speed test: a 1280 x 720 poster and a stand-in video file.

    The ids are fixed, so a run is repeatable. Pillow is imported here, only for this option.
    """
    from PIL import Image

    video_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "backdrop.video.sharmadental.in"))
    poster_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "backdrop.poster.sharmadental.in"))
    folder = out / "_landing" / "media"
    folder.mkdir(parents=True, exist_ok=True)
    Image.linear_gradient("L").resize((1280, 720)).convert("RGB").save(
        folder / f"{poster_id}.webp", "WEBP", quality=60
    )
    (folder / f"{video_id}.mp4").write_bytes(
        bytes([0, 0, 0, 24]) + b"ftypmp42" + bytes(4) + b"mp42isom"
    )
    home = data["home"]
    assert isinstance(home, list)
    for section in home:
        if section["type"] == "hero":
            section["backdrop_video"] = True
    home.insert(
        -1,
        {
            "id": "film",
            "type": "video",
            "enabled": True,
            "video_asset_id": video_id,
            "poster_asset_id": poster_id,
            "placement": "hero_backdrop",
        },
    )
    return {video_id: f"{video_id}.mp4", poster_id: f"{poster_id}.webp"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the public website to static files.")
    parser.add_argument("--content", default="default", help="default, preset:<key> or a JSON file")
    parser.add_argument("--brand", help="brand colour, e.g. '#0f766e'")
    parser.add_argument("--base", choices=["light", "dark", "auto"], help="base theme")
    parser.add_argument("--lang", default="en", help="en, hi, hi-Latn or all")
    parser.add_argument("--out", required=True, help="folder to write into")
    parser.add_argument(
        "--pages", help="comma-separated pages to write (home,pricing,...); default every page"
    )
    parser.add_argument("--ask-key", help="a public key: switches 'Ask it yourself' on")
    parser.add_argument(
        "--no-whatsapp", action="store_true", help="render as if no plan sold WhatsApp"
    )
    parser.add_argument(
        "--backdrop-demo",
        action="store_true",
        help="add a hero background video with a large poster (placeholder files are written)",
    )
    args = parser.parse_args(argv)

    content = load_content(args.content)
    data = content.model_dump(mode="json")
    if args.brand:
        data["theme"]["brand_color"] = args.brand
    if args.base:
        data["theme"]["base"] = args.base
    if args.ask_key:
        data["ask"]["enabled"] = True
    media: dict[str, str] = {}
    if args.backdrop_demo:
        media = _backdrop_demo(data, Path(args.out))
    content = LandingContent.model_validate(data)
    languages: list[str] = list(content.languages) if args.lang == "all" else [args.lang]
    for lang in languages:
        if lang not in content.languages:
            sys.stderr.write(f"this content has no {lang} version\n")
            return 2
    env = example_environment(ask_key=args.ask_key, whatsapp=not args.no_whatsapp)
    env = replace(env, media=media)
    wanted = args.pages.split(",") if args.pages else None
    files = render_site(content, Path(args.out), languages=languages, env=env, pages=wanted)
    sys.stdout.write(f"{len(files)} pages written to {args.out}\n")
    return 0


# LP3 landing
def example_pricing_data() -> "PricingData":
    """Three rupee plans, three add-ons and WhatsApp rates: the example the snapshot, the render
    tests and the screenshots share. Ids are fixed so a page renders the same every time."""
    import uuid

    from callobuzz_rag.landing.pricing import (
        AddOnData,
        PlanData,
        PriceData,
        PricingData,
        RateData,
    )

    def plan_id(name: str) -> uuid.UUID:
        return uuid.uuid5(uuid.NAMESPACE_DNS, f"{name}.sharmadental.in")

    def price(
        fee: int,
        per_question: int,
        credit: int,
        *,
        currency: str = "INR",
        period: str = "monthly",
    ) -> PriceData:
        return PriceData(
            currency=currency,
            period=period,
            fee_minor=fee,
            usage_mode="per_question" if per_question else "included",
            per_question_minor=per_question,
            included_credit_minor=credit,
            overage="block",
            overage_mode="per_question",
            overage_per_question_minor=0,
            placeholder=False,
        )

    plans = (
        PlanData(
            plan_id("Free"),
            "Free",
            "For a clinic or shop getting started",
            1,
            100,
            20,
            1,
            False,
            price(0, 0, 0),
        ),
        PlanData(
            plan_id("Standard"),
            "Standard",
            "When leads matter as much as answers",
            3,
            None,
            500,
            3,
            False,
            price(99_900, 200, 50_000),
        ),
        PlanData(
            plan_id("Pro"),
            "Pro",
            "For agencies and multi-branch businesses",
            20,
            None,
            None,
            20,
            True,
            price(499_900, 150, 300_000),
        ),
    )
    extra_plans = (  # only used by cards that name them (the toggles' example)
        PlanData(
            plan_id("Standard yearly"),
            "Standard yearly",
            "",
            3,
            None,
            500,
            3,
            False,
            price(990_000, 200, 50_000, period="yearly"),
        ),
        PlanData(
            plan_id("Standard USD"),
            "Standard USD",
            "",
            3,
            None,
            500,
            3,
            False,
            price(1_500, 3, 600, currency="USD"),
        ),
    )
    add_ons = (
        AddOnData("feature_unlock", "Own AI key", "", "INR", 49_900, None, 0, None, "byok", True),
        AddOnData("credit_pack", "Top-up 1000", "", "INR", 100_000, 110_000, 10, None, None, False),
        AddOnData(
            "service",
            "Website design",
            "We build and connect it for you",
            "INR",
            1_500_000,
            None,
            0,
            None,
            None,
            False,
        ),
    )
    return PricingData(
        plans=(*plans, *extra_plans),
        sheet_plan_ids=tuple(p.id for p in plans),
        add_ons=add_ons,
        rates=(RateData("INR", 0, 12, 78, 12),),
        gst_registered=True,
    )


# /LP3 landing


if __name__ == "__main__":
    raise SystemExit(main())
