import re

import pytest

from callobuzz_rag.demo import seed_data


def test_the_seed_loads_and_validates() -> None:
    seed = seed_data.load()
    assert seed.business.name == "Nirmal Family Clinic"
    assert {d.lang for d in seed.documents} == {"en", "hi"}
    assert len(seed.documents) == 5 and len(seed.faq) == 6 and len(seed.questions) == 6
    assert all(d.external_id.startswith("demo:") for d in seed.documents)
    assert (
        seed.plan.name == "Demo" and seed.plan.body["fair_use_limits"]["questions_per_day"] == 300
    )
    assert seed.engine.settings["pipelines"]["engine_ai_spend_per_day_usd_micro"] == 2_000_000
    assert seed.engine.settings["branding"]["product_name"] == "Sahayak Studio"
    assert seed.engine.settings["signup_mode"] == "closed"


def test_hindi_is_really_hindi() -> None:
    hi = next(d for d in seed_data.load().documents if d.lang == "hi")
    assert len(re.findall(r"[ऀ-ॿ]", hi.text)) > 200
    assert "108" in hi.text


def test_faq_is_three_english_and_three_hindi() -> None:
    faq = seed_data.load().faq
    assert [f.lang for f in faq].count("en") == 3 and [f.lang for f in faq].count("hi") == 3


def test_no_real_contact_details_or_words_we_never_show() -> None:
    seed = seed_data.load()
    blob = seed_data.all_text(seed)
    for word in ("corpus", "tenant", "lorem", "TODO"):
        assert word.lower() not in blob.lower(), word
    phones = set(re.findall(r"\+91[\d ]{10,14}", blob))
    assert phones <= {"+91 98765 43210"}
    for email in re.findall(r"[\w.+-]+@[\w.-]+", blob):
        assert email.endswith(".example.com"), email


def test_plan_body_matches_the_plan_schema() -> None:
    from callobuzz_rag.api.schemas.plan import CreatePlanRequest

    CreatePlanRequest(**seed_data.load().plan.body)


def test_assistant_has_the_safety_rules() -> None:
    rules = " ".join(seed_data.load().assistant.rules).lower()
    assert "diagnose" in rules and "108" in rules


def test_every_sample_question_is_in_a_language_the_assistant_supports() -> None:
    seed = seed_data.load()
    supported = set(seed.assistant.patch["language"]["supported"])
    assert {q.lang for q in seed.questions} <= supported


def test_the_widget_demo_questions_include_the_safety_rule_case() -> None:
    texts = [q.text for q in seed_data.load().questions]
    assert any("chest pain" in t for t in texts)


def test_a_broken_file_names_itself(tmp_path: pytest.TempPathFactory) -> None:
    import shutil
    from pathlib import Path

    src = Path(str(seed_data.DATA))
    dst = Path(str(tmp_path)) / "clinic"
    shutil.copytree(src, dst)
    (dst / "plan.json").write_text('{"name": "Demo", "max_knowledge_bases": 0}', "utf-8")
    with pytest.raises(seed_data.SeedError, match="schema"):
        seed_data.load(dst)  # type: ignore[arg-type]
    (dst / "plan.json").write_text("{not json", "utf-8")
    with pytest.raises(seed_data.SeedError, match=r"plan\.json"):
        seed_data.load(dst)  # type: ignore[arg-type]
