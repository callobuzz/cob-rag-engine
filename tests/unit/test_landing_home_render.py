"""LP2 T3, T4, T6: the home page's sections, the narrator thread, the WhatsApp chapter with its
e-mail fallback and the optional video."""

import re
import urllib.parse
import uuid
from typing import Any

import pytest

from callobuzz_rag.landing.presets import PRESET_KEYS, preset_site
from callobuzz_rag.landing.schema import LandingContent
from tests.unit.landing_fakes import content_with
from tests.unit.landing_home_fakes import parse, render_home, section_order, section_texts


def _mutate(fn: Any) -> LandingContent:
    return content_with(fn)


def _section(data: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(s for s in data["home"] if s["type"] == kind)


# ------------------------------------------------------------------ order and the narrator


def test_the_section_order_follows_the_content() -> None:
    html = render_home()
    order = section_order(html)
    assert order[0] == "hero" and order[-1] == "final_cta"
    content = content_with()
    assert order == [s.type for s in content.home if s.enabled]


def test_a_disabled_section_and_its_narrator_bubble_are_absent() -> None:
    def off(data: dict[str, Any]) -> None:
        _section(data, "grounded")["enabled"] = False

    html = render_home(_mutate(off))
    assert 'data-section="grounded"' not in html
    assert "I answer only from your own pages" not in html


def test_every_narrator_bubble_precedes_its_section_in_the_dom() -> None:
    html = render_home()
    root = parse(html)
    beats = root.find_all(cls="beat")
    assert len(beats) >= 8
    for beat in beats:
        kids = [c for c in beat.children]
        assert kids[0].tag == "div" and "narrator" in kids[0].classes()
        assert kids[1].tag == "section"
    # No bubble is left loose: it sits in a beat, or inside the section that places it itself
    # (the pricing teaser), before that section's heading.
    for narrator in root.find_all("div", "narrator"):
        assert any("beat" in a.classes() or a.tag == "section" for a in narrator.ancestors())


def test_a_section_that_renders_nothing_takes_its_bubble_with_it() -> None:
    def video(data: dict[str, Any]) -> None:
        data["home"].append(
            {
                "id": "demo",
                "type": "video",
                "enabled": True,
                "narrator": {"en": "A short film about us."},
                "video_asset_id": str(uuid.uuid4()),
                "poster_asset_id": str(uuid.uuid4()),
            }
        )
        # keep the last section final_cta
        data["home"].insert(-2, data["home"].pop())

    html = render_home(_mutate(video))  # no media in the environment: the file is "deleted"
    assert "A short film about us." not in html
    assert "<video" not in html


# ------------------------------------------------------------------ the sections


def test_pain_wall_items_carry_channel_tags_and_a_12_hour_time() -> None:
    root = parse(render_home())
    wall = next(s for s in root.find_all("section") if s.attrs.get("data-section") == "pain_wall")
    items = wall.find_all("li", "question")
    assert items
    tags = [i.find_all(cls="tag")[0].all_text() for i in items]
    assert set(tags) == {"Website", "WhatsApp"}
    times = [i.find_all("time")[0] for i in items]
    assert times[0].attrs["datetime"] == "22:14" and times[0].all_text() == "10:14 pm"


def test_languages_shows_three_samples_with_lang_attributes() -> None:
    root = parse(render_home())
    block = next(s for s in root.find_all("section") if s.attrs.get("data-section") == "languages")
    samples = block.find_all("li", "sample")
    assert [s.attrs["lang"] for s in samples] == ["en", "hi", "hi-Latn"]
    assert re.search(r"[ऀ-ॿ]", samples[1].all_text())
    assert not re.search(r"[ऀ-ॿ]", samples[2].all_text())


def test_value_report_says_estimated_next_to_the_value() -> None:
    html = render_home()
    block = next(t for t in section_texts(html) if "Estimated value" in t)
    assert "₹46,000" in block and "estimated" in block.casefold()
    assert (
        'class="count"' in html and 'style="--to: 412"' in html
    )  # the final number is in the HTML


def test_human_handoff_shows_the_reference_and_the_prefilled_whatsapp_text() -> None:
    html = render_home()
    assert "K7-4PQM" in html
    prefill = (
        "Hi, I was chatting on your website. My reference is K7-4PQM. "
        "I asked about fees and would like to speak to someone."
    )
    assert "https://wa.me/919822012345?text=" + urllib.parse.quote(prefill, safe="") in html


def test_faq_uses_details_and_summary() -> None:
    root = parse(render_home())
    faq = next(s for s in root.find_all("section") if s.attrs.get("data-section") == "faq")
    details = faq.find_all("details")
    assert len(details) >= 5 and all(d.find_all("summary") for d in details)


def test_final_cta_puts_the_button_inside_the_last_chat_bubble() -> None:
    root = parse(render_home())
    cta = next(s for s in root.find_all("section") if s.attrs.get("data-section") == "final_cta")
    bubble = cta.find_all(cls="cta-bubble")[0]
    inside = [a for a in bubble.find_all("a") if "btn" in a.classes()]
    assert inside and inside[0].attrs["href"] == "/signup"
    below = [a for a in cta.find_all("a", "btn") if bubble not in a.ancestors()]
    assert below, "a normal button stays below the bubble"


def test_bento_has_at_most_seven_tiles_and_no_canvas_anywhere() -> None:
    html = render_home()
    root = parse(html)
    bento = next(s for s in root.find_all("section") if s.attrs.get("data-section") == "bento")
    assert 3 <= len(bento.find_all("li", "tile")) <= 7
    assert "<canvas" not in html


def test_headings_are_in_order() -> None:
    root = parse(render_home())
    tags = [n.tag for n in root.walk() if n.tag in ("h1", "h2", "h3")]
    assert tags.count("h1") == 1 and tags[0] == "h1"
    # an h3 never appears before the section's own h2
    for section in root.find_all("section"):
        seq = [n.tag for n in section.walk() if n.tag in ("h1", "h2", "h3")]
        if "h3" in seq and section.attrs.get("data-section") != "hero":
            assert seq.index("h2") < seq.index("h3"), section.attrs["data-section"]


def test_the_hero_headline_is_two_tone() -> None:
    html = render_home()
    assert '<span class="h-lead">Answers customers day and night.</span>' in html
    assert '<span class="h-rest">From your own content.</span>' in html


@pytest.mark.parametrize("key", PRESET_KEYS)
def test_every_preset_site_renders_in_every_language(key: str) -> None:
    site = preset_site(key)
    for lang in ("en", "hi", "hi-Latn"):
        html = render_home(site, lang=lang)
        assert html.count("<h1") == 1
        order = section_order(html)
        assert "whatsapp_journey" in order
        assert 'data-variant="whatsapp"' in html
        hero_chat = html.split('data-section="hero"')[1].split('data-section="trust_strip"')[0]
        assert hero_chat.count('<li class="bubble bubble-') >= 8  # the same turns on both screens


# ------------------------------------------------------------------ the WhatsApp chapter


def test_whatsapp_chapter_when_a_listed_plan_sells_whatsapp() -> None:
    html = render_home(whatsapp_on_sale=True)
    assert 'data-variant="whatsapp"' in html and "11:42" in html


def test_email_handoff_variant_and_no_whatsapp_promise_without_it() -> None:
    from callobuzz_rag.core.company import CompanyDetails

    company = CompanyDetails(legal_name="Sharma Dental Clinic", phone="+91 98220 12345")
    html = render_home(whatsapp_on_sale=False, company=company)
    assert 'data-variant="email_handoff"' in html
    # The deployer's own "Talk to us on WhatsApp" contact link is their phone, not a feature.
    sections = section_texts(html, exclude_selector=".contact-link")
    assert not any("whatsapp" in text.casefold() for text in sections), [
        t for t in sections if "whatsapp" in t.casefold()
    ]
    assert 'data-visual="website"' in html
    assert 'class="phone"' not in html
    assert "An example conversation" in html
    assert 'class="email-card"' in html and "tel:" in html


def test_a_hero_that_wants_whatsapp_falls_back_to_the_website_without_it() -> None:
    def wa(data: dict[str, Any]) -> None:
        _section(data, "hero")["visual"] = "whatsapp"

    html = render_home(_mutate(wa), whatsapp_on_sale=False)
    assert 'data-visual="website"' in html and 'class="site-card"' in html


def test_whatsapp_link_only_with_a_company_phone() -> None:
    from callobuzz_rag.core.company import CompanyDetails

    no_phone = CompanyDetails(legal_name="Sharma Dental Clinic")
    assert "https://wa.me/" not in render_home(whatsapp_on_sale=True, company=no_phone)
    assert "https://wa.me/919822012345" in render_home(whatsapp_on_sale=True)


def test_the_phone_appears_in_the_hero_only_when_whatsapp_is_sold() -> None:
    assert 'class="phone"' in render_home(whatsapp_on_sale=True)
    assert 'data-visual="both"' in render_home(whatsapp_on_sale=True)


# ------------------------------------------------------------------ video


def _with_video(placement: str, *, backdrop: bool = False) -> tuple[LandingContent, dict[str, str]]:
    video_id, poster_id = uuid.uuid4(), uuid.uuid4()

    def add(data: dict[str, Any]) -> None:
        data["home"].insert(
            -2,
            {
                "id": "film",
                "type": "video",
                "enabled": True,
                "heading": {"en": "See it in a minute"},
                "narrator": {"en": "A minute of video."},
                "video_asset_id": str(video_id),
                "poster_asset_id": str(poster_id),
                "caption": {"en": "A walk through the set-up."},
                "placement": placement,
            },
        )
        _section(data, "hero")["backdrop_video"] = backdrop

    media = {str(video_id): f"{video_id}.mp4", str(poster_id): f"{poster_id}.webp"}
    return _mutate(add), media


def test_a_video_section_has_a_poster_and_no_source_before_javascript() -> None:
    content, media = _with_video("section")
    html = render_home(content, media=media)
    video = next(n for n in parse(html).find_all("video"))
    assert {"muted", "playsinline", "loop"} <= set(video.attrs)
    assert video.attrs["preload"] == "none"
    assert video.attrs["poster"].startswith("/_landing/media/") and video.attrs["poster"].endswith(
        ".webp"
    )
    sources = video.find_all("source")
    assert (
        sources and "src" not in sources[0].attrs and sources[0].attrs["data-src"].endswith(".mp4")
    )
    assert "A walk through the set-up." in html
    assert "data-video-toggle" in html  # a pause button is always there
    assert "controls" not in video.attrs


def test_no_video_section_and_no_backdrop_means_no_video_element() -> None:
    assert "<video" not in render_home()


def test_a_hero_backdrop_gets_its_poster_only_after_load() -> None:
    content, media = _with_video("hero_backdrop", backdrop=True)
    html = render_home(content, media=media)
    video = next(n for n in parse(html).find_all("video"))
    assert "poster" not in video.attrs  # the poster is attached by the script after `load`
    assert video.attrs["data-poster"].endswith(".webp") and "data-backdrop" in video.attrs
    assert html.count("<video") == 1  # the section itself renders nothing for a backdrop
    assert "A minute of video." not in html  # so its narrator bubble is gone too


def test_a_backdrop_flag_without_a_backdrop_video_renders_no_video() -> None:
    def flag(data: dict[str, Any]) -> None:
        _section(data, "hero")["backdrop_video"] = True

    assert "<video" not in render_home(_mutate(flag))


# ------------------------------------------------------------------ LP2 review fixes

_WA_WORD = re.compile(r"whatsapp|व्हाट्स|वॉट्स|व्हॉट्स", re.IGNORECASE)


def _main_texts(html: str) -> list[str]:
    """Every text node inside <main>, leaving out the deployer's own contact links."""
    main = parse(html).find_all("main")[0]
    out: list[str] = []
    for node in main.walk():
        if any("contact-link" in n.classes() for n in [node, *node.ancestors()]):
            continue
        out += [p.strip() for p in node.text if p.strip()]
    return out


@pytest.mark.parametrize("key", PRESET_KEYS)
def test_no_whatsapp_promise_in_any_language_or_preset_without_it(key: str) -> None:
    for lang in ("en", "hi", "hi-Latn"):
        html = render_home(preset_site(key), lang=lang, whatsapp_on_sale=False)
        bad = [t for t in _main_texts(html) if _WA_WORD.search(t)]
        assert not bad, (key, lang, bad)


def test_the_narrator_of_the_journey_does_not_name_whatsapp_without_it() -> None:
    def leak(data: dict[str, Any]) -> None:
        _section(data, "whatsapp_journey")["narrator"] = {"en": "Now the WhatsApp chapter"}

    html = render_home(_mutate(leak), whatsapp_on_sale=False)
    assert "WhatsApp chapter" not in html
    assert "WhatsApp chapter" in render_home(_mutate(leak), whatsapp_on_sale=True)


def _primary_links(node: Any) -> list[str]:
    return [a.attrs["href"] for a in node.find_all("a", "btn-primary")]


def test_without_sign_up_the_hero_and_the_closing_bubble_still_hold_one_button() -> None:
    from callobuzz_rag.core.company import CompanyDetails

    company = CompanyDetails(legal_name="Sharma Dental Clinic")
    root = parse(render_home(signup_available=False, company=company))
    for kind in ("hero", "final_cta"):
        sec = next(s for s in root.find_all("section") if s.attrs.get("data-section") == kind)
        scope = sec.find_all(cls="cta-bubble")[0] if kind == "final_cta" else sec
        links = _primary_links(scope)
        assert len(links) == 1 and "signup" not in links[0], (kind, links)


def test_the_phone_mirror_is_hidden_from_assistive_tech_next_to_the_site_card() -> None:
    root = parse(render_home(whatsapp_on_sale=True))
    hero = next(s for s in root.find_all("section") if s.attrs.get("data-section") == "hero")
    visible = [
        c
        for c in hero.find_all(cls="chat")
        if not any(a.attrs.get("aria-hidden") == "true" for a in c.ancestors())
    ]
    assert len(visible) == 1


def test_the_narrator_is_a_note_not_a_landmark() -> None:
    root = parse(render_home())
    assert not root.find_all("aside")
    assert root.find_all(cls="narrator")[0].attrs.get("role") == "note"


def test_a_backdrop_video_has_a_pause_button_outside_the_hidden_backdrop() -> None:
    content, media = _with_video("hero_backdrop", backdrop=True)
    root = parse(render_home(content, media=media))
    assert root.find_all("video")
    toggles = root.find_all("button", "video-toggle")
    assert len(toggles) == 1
    assert not any(a.attrs.get("aria-hidden") == "true" for a in toggles[0].ancestors())
