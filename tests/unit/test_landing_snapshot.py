"""LP1 T8: the database-free snapshot renderer."""

import json
from pathlib import Path

import pytest

from callobuzz_rag.landing import snapshot
from callobuzz_rag.landing.schema import default_content


def test_the_pricing_fixture_is_a_rupee_price_list() -> None:
    plans = snapshot.fixture_plans()
    assert [p.name for p in plans] == ["Free", "Standard", "Pro"]
    assert plans[1].price == "₹999 a month" and "incl. GST" in (plans[1].price_incl or "")


def test_every_enabled_page_is_written(tmp_path: Path) -> None:
    written = snapshot.render_site(default_content(), tmp_path, languages=["en"])
    names = sorted(p.relative_to(tmp_path).as_posix() for p in written)
    assert names == [
        "about/index.html",
        "contact/index.html",
        "features/index.html",
        "index.html",
        "pricing/index.html",
    ]
    assert "Standard" in (tmp_path / "pricing" / "index.html").read_text("utf-8")


def test_a_disabled_page_is_not_written_and_other_languages_get_a_folder(tmp_path: Path) -> None:
    data = default_content().model_dump(mode="json")
    data["languages"] = ["en", "hi"]
    data["about"]["enabled"] = False
    from callobuzz_rag.landing.schema import LandingContent

    written = snapshot.render_site(LandingContent.model_validate(data), tmp_path, languages=["hi"])
    names = sorted(p.relative_to(tmp_path).as_posix() for p in written)
    assert "hi/index.html" in names and "hi/pricing/index.html" in names
    assert not any("about" in n for n in names)
    assert 'lang="hi"' in (tmp_path / "hi" / "index.html").read_text("utf-8")


def test_the_command_line_applies_brand_and_base(tmp_path: Path) -> None:
    code = snapshot.main(
        [
            "--content",
            "default",
            "--brand",
            "#7c3aed",
            "--base",
            "dark",
            "--lang",
            "en",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    html = (tmp_path / "index.html").read_text("utf-8")
    assert 'data-theme-base="dark"' in html and "--glow-a:#7c3aed" in html


def test_a_language_the_content_lacks_is_refused(tmp_path: Path) -> None:
    assert snapshot.main(["--lang", "hi", "--out", str(tmp_path)]) == 2


def test_content_can_come_from_a_json_file(tmp_path: Path) -> None:
    data = default_content().model_dump(mode="json")
    data["contact"]["headline"] = {"en": "Write to the clinic"}
    source = tmp_path / "site.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out"
    assert snapshot.main(["--content", str(source), "--out", str(out)]) == 0
    assert "Write to the clinic" in (out / "contact" / "index.html").read_text("utf-8")


def test_an_unknown_preset_is_a_clean_exit() -> None:
    with pytest.raises(SystemExit):
        snapshot.load_content("preset:nope")


def test_a_known_preset_renders_in_hinglish(tmp_path: Path) -> None:
    code = snapshot.main(["--content", "preset:store", "--lang", "hi-Latn", "--out", str(tmp_path)])
    assert code == 0
    assert "Teak wala 6-seater" in (tmp_path / "hi-latn" / "index.html").read_text("utf-8")


# LP2 landing: the options the browser specs use
def test_pages_ask_key_and_no_whatsapp_options(tmp_path: Path) -> None:
    code = snapshot.main(
        [
            "--content",
            "preset:clinic",
            "--pages",
            "home",
            "--ask-key",
            "pk_demo",
            "--no-whatsapp",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_dir()) == ["_landing"]
    html = (tmp_path / "index.html").read_text("utf-8")
    assert 'data-ask-key="pk_demo"' in html and 'data-variant="email_handoff"' in html


def test_the_backdrop_demo_writes_a_large_poster_and_a_video_stand_in(tmp_path: Path) -> None:
    code = snapshot.main(
        [
            "--content",
            "preset:services",
            "--pages",
            "home",
            "--backdrop-demo",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    media = sorted(p.suffix for p in (tmp_path / "_landing" / "media").iterdir())
    assert media == [".mp4", ".webp"]
    html = (tmp_path / "index.html").read_text("utf-8")
    assert "data-backdrop" in html and html.count("<video") == 1
    from PIL import Image

    poster = next((tmp_path / "_landing" / "media").glob("*.webp"))
    assert Image.open(poster).size == (1280, 720)
