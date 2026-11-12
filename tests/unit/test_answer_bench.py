"""benchmarks/answers.py: the answer-model harness, proven with the fake model (no paid calls).

Record validation, resumability after a kill, the budget cap, fail-fast, retries, the thinking
adapter, the prompt (checked against the engine's own assembler), every scorer, the dry run end
to end and the smoke mode's exit codes.
"""

import asyncio
import contextlib
import json
import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import answers as cli  # noqa: E402
from benchmarks.answer_bench import ITEMS_PATH, build_items, capture, generate, score  # noqa: E402
from benchmarks.answer_bench import thinking as th  # noqa: E402
from benchmarks.answer_bench.fake import FakeLookup, FakeModel, answer_for, fake_cost  # noqa: E402
from benchmarks.answer_bench.items import Item, load_items, select_items  # noqa: E402
from benchmarks.answer_bench.prompt import compiled, fingerprint, user_message  # noqa: E402
from benchmarks.answer_bench.records import (  # noqa: E402
    AnswerRecord,
    Progress,
    append_jsonl,
    read_jsonl,
    write_jsonl,
)

from callobuzz_rag.core.config import Settings  # noqa: E402


@pytest.fixture(scope="module")
def items() -> list[Item]:
    return load_items()


def _cfg(tmp: Path, items: list[Item], models: str = "fake/good", **kw: Any) -> generate.RunConfig:
    defaults: dict[str, Any] = {
        "run": tmp.name,
        "out_dir": tmp,
        "models": generate.parse_models(models),
        "variants": ["off"],
        "items": items,
        "items_sha256": "test-sha",
        "settings": Settings(),
        "api_key": "dry-run",
        "api_base": None,
        "provider": "openrouter",
        "temperature": 0.7,
        "budget_usd": 1.0,
        "backoff_s": 0.001,
        "dry_run": True,
        "inner": FakeModel(items).acompletion,
        "lookup": FakeLookup(),
        "estimate": lambda m, v, p: fake_cost(p, 600),
        "log": lambda m: None,
    }
    defaults.update(kw)
    return generate.RunConfig(**defaults)


def _run(cfg: generate.RunConfig) -> Progress:
    return asyncio.run(generate.Runner(cfg).run())


def _answers(tmp: Path) -> list[AnswerRecord]:
    return read_jsonl(tmp / "answers.jsonl", AnswerRecord)


# ---------------------------------------------------------------------------- the item set


def test_item_set_composition(items: list[Item]) -> None:
    def n(lang: str | None = None, kind: str | None = None) -> int:
        return sum(1 for i in items if lang in (None, i.language) and kind in (None, i.type))

    assert n("en", "answerable") == 40
    assert n("hi") == 20 and n("hi-Latn") == 20
    assert n(kind="unanswerable") == 16 and n(kind="table") == 10
    assert all(3 <= len(i.passages) <= 6 for i in items)
    assert all(i.native_review == "pending" for i in items if i.language != "en")
    translated = [p for i in items for p in i.passages if p.translated]
    assert translated and all(p.native_review == "pending" and p.translation_of for p in translated)


def test_item_set_is_the_builder_output() -> None:
    """items.jsonl is frozen: it must equal what build_items produces (no hand edits)."""
    built = [json.dumps(i.model_dump(), ensure_ascii=False) for i in build_items.build()]
    assert ITEMS_PATH.read_text("utf-8").splitlines() == built


def test_item_set_is_consistent() -> None:
    """Each item has one correct answer derivable from its passages, in rupees (see the rules in
    build_items.py: gold facts present, no conflicting value, unanswerable really unanswerable,
    rupees only, table facts in the table)."""
    assert build_items.audit() == []


def test_item_set_has_only_rupees(items: list[Item]) -> None:
    text = json.dumps([i.model_dump() for i in items], ensure_ascii=False)
    assert not re.search(r"dollar|डॉलर|\$|USD|€|£", text, re.I)
    assert "Ridgeway" not in text and "capital cit" not in text


def _synthetic(
    monkeypatch: pytest.MonkeyPatch,
    texts: dict[str, str],
    gold: list[str],
    facts: list[list[str]],
    **kw: Any,
) -> tuple[build_items.Q, list[build_items.P]]:
    """A one-off question over made-up passages, registered where the checker looks them up."""
    passages = [
        build_items.P(pid, "Acme", "f.md", "Section", None, text) for pid, text in texts.items()
    ]
    for p in passages:
        monkeypatch.setitem(build_items.BY_ID, p.id, p)
    q = build_items.Q(
        "t-1", "en", kw.pop("type", "answerable"), "How much is the resole?", gold, facts, **kw
    )
    return q, passages


def test_checker_passes_a_clean_item(monkeypatch: pytest.MonkeyPatch) -> None:
    q, ps = _synthetic(
        monkeypatch,
        {"g": "Resoling costs Rs 1,800 at our partner workshop.", "d": "Zips cost Rs 450."},
        ["g"],
        [["1,800"]],
        forbidden=["Rs 2,200"],
    )
    assert build_items.check_item(q, ps) == []


def test_checker_catches_a_conflicting_value(monkeypatch: pytest.MonkeyPatch) -> None:
    q, ps = _synthetic(
        monkeypatch,
        {
            "g": "Resoling costs Rs 1,800 at our partner workshop.",
            "d": "Boot resoling at the partner workshop is Rs 1,500 this month.",
        },
        ["g"],
        [["1,800"]],
        attr=["resol"],
    )
    assert any("states [1500.0] rs" in v for v in build_items.check_item(q, ps))
    # the same figure in the other passage is not a conflict
    ps[1] = build_items.P("d", "Acme", "f.md", "S", None, "Boot resoling costs Rs 1,800.")
    monkeypatch.setitem(build_items.BY_ID, "d", ps[1])
    assert build_items.check_item(q, ps) == []


def test_checker_catches_a_conflicting_form_code_and_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q, ps = _synthetic(
        monkeypatch,
        {
            "g": "Start the warranty claim online with claim form W-3 and wait for the review.",
            "d": "Start the warranty claim online with claim form T-12 and wait for the review.",
        },
        ["g"],
        [["w-3"]],
    )
    assert any("code:W-3" in v for v in build_items.check_item(q, ps))
    q, ps = _synthetic(
        monkeypatch,
        {
            "g": "Warranty claims are assessed within 10 business days of arrival.",
            "d": "Warranty claims are assessed within 7 business days of arrival.",
        },
        ["g"],
        [["10 business days"]],
        attr=["assessed"],
    )
    assert any("states [7.0] day" in v for v in build_items.check_item(q, ps))


def test_checker_catches_a_missing_gold_fact_and_other_currencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q, ps = _synthetic(
        monkeypatch, {"g": "Resoling costs Rs 1,800.", "d": "Zips cost 5 dollars."}, ["g"], [["65"]]
    )
    found = build_items.check_item(q, ps)
    assert any("in no gold passage" in v for v in found)
    assert any("foreign currency" in v for v in found)


def test_checker_catches_a_price_column_without_rupees_and_a_bare_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = "| Name | Price |\n|---|---|\n| Tent | 349.00 |\n| Bag | 129.00 |"
    q, ps = _synthetic(
        monkeypatch, {"g": table, "d": "The fee is 6.95 per parcel."}, ["g"], [["349"]]
    )
    found = build_items.check_item(q, ps)
    assert any("without Rs" in v for v in found)
    assert any("bare amount" in v for v in found)


def test_checker_holds_unanswerable_items_to_their_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    texts = {
        "g": "Staff get a 35 percent discount.",
        "d": "Students must show an ID card.",
        "e": "Delivery takes 3 days.",
    }
    q, ps = _synthetic(
        monkeypatch,
        texts,
        ["g"],
        [],
        type="unanswerable",
        absent=["student"],
        forbidden=["35 percent"],
    )
    found = build_items.check_item(q, ps)
    assert any("would answer the question" in v for v in found)
    assert any("not declared bait" in v for v in found)
    q, ps = _synthetic(
        monkeypatch,
        texts,
        ["g"],
        [],
        type="unanswerable",
        absent=["voucher"],
        forbidden=["35 percent"],
        bait=["35 percent"],
    )
    assert build_items.check_item(q, ps) == []


def test_checker_wants_table_facts_in_the_table_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    table = "| Zone | Fee (Rs) |\n|---|---|\n| A | 99 |"
    q, ps = _synthetic(
        monkeypatch,
        {"g": "Zone B costs Rs 149 in prose.\n\n" + table},
        ["g"],
        [["99"], ["149"]],
        type="table",
    )
    found = build_items.check_item(q, ps)
    assert [v for v in found if "not in the table rows" in v] == [
        "t-1: table fact ['149'] is not in the table rows"
    ]


def test_pool_check_catches_two_prices_for_one_service() -> None:
    a = build_items.P("a", "Acme", "f", None, None, "Boot resoling costs Rs 1,800.")
    b = build_items.P("b", "Beta", "f", None, None, "Boot resoling costs Rs 65.")
    assert build_items.check_pool([a]) == []
    assert any("boot resoling price" in v for v in build_items.check_pool([a, b]))


def test_gold_facts_are_in_the_gold_passages_and_forbidden_ones_are_not(items: list[Item]) -> None:
    for item in items:
        gold = " ".join(item.passages[g - 1].text for g in item.gold_passages)
        gold += " " + " ".join(item.passages[g - 1].section or "" for g in item.gold_passages)
        for fact in item.gold_facts:
            assert any(score.contains(gold, form) for form in fact), (item.id, fact)
        for wrong in item.forbidden_facts:
            assert not score.contains(gold, wrong), (item.id, wrong)


# ---------------------------------------------------------------------------- records


def _record(**over: Any) -> dict[str, Any]:
    base = AnswerRecord.model_json_schema()["properties"]
    data: dict[str, Any] = {
        "harness_version": "1",
        "run": "r",
        "timestamp": "t",
        "model": "m",
        "provider": "p",
        "provider_order": [],
        "host": "h",
        "host_source": "fake",
        "generation_id": "g",
        "variant": "off",
        "thinking_effective": "off",
        "thinking_source": "engine",
        "item_id": "i",
        "item_hash": "x",
        "language": "en",
        "item_type": "answerable",
        "question": "q",
        "answer_raw": "a",
        "answer_released": "a",
        "route": "grounded",
        "gate_removed": 0,
        "ttft_ms": 5,
        "first_release_ms": 5,
        "thinking_ms": None,
        "total_ms": 9,
        "tokens_in": 1,
        "tokens_out": 1,
        "tokens_reasoning": 0,
        "reasoning_seen": False,
        "cost_usd": 0.1,
        "cost_known": True,
        "cost_source": "provider_usage",
        "finish_reason": "stop",
        "error": None,
        "error_kind": None,
        "retries": 0,
        "request_params": {},
        "request_params_hash": "h",
        "prompt_hash": "h",
        "rail_failures": [],
        "warnings": [],
    }
    assert set(data) == set(base)
    data.update(over)
    return data


def test_record_requires_every_field_and_forbids_extras() -> None:
    AnswerRecord.model_validate(_record())
    for field in _record():
        broken = _record()
        del broken[field]
        with pytest.raises(ValidationError):
            AnswerRecord.model_validate(broken)
    with pytest.raises(ValidationError):
        AnswerRecord.model_validate(_record(surprise=1))
    with pytest.raises(ValidationError):  # an error record must say so in its route
        AnswerRecord.model_validate(_record(error="boom"))
    with pytest.raises(ValidationError):
        AnswerRecord.model_validate(_record(cost_usd=None, cost_known=True))


def test_read_jsonl_repairs_only_a_torn_last_line(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    append_jsonl(path, AnswerRecord.model_validate(_record(item_id="one")))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"harness_version": "1", "run"')  # killed mid-write
    assert [r.item_id for r in read_jsonl(path, AnswerRecord, repair_tail=True)] == ["one"]
    assert path.read_text("utf-8").endswith("\n")
    path.write_text("not json\n" + path.read_text("utf-8"), encoding="utf-8")
    with pytest.raises(ValueError):
        read_jsonl(path, AnswerRecord, repair_tail=True)


# ---------------------------------------------------------------------------- prompt


def test_user_message_matches_the_engines_context_assembler(items: list[Item]) -> None:
    from callobuzz_rag.retrieval.context_assembly import ContextAssembler, ContextSearchResult
    from callobuzz_rag.retrieval.safety import ContentSanitizer

    settings = Settings()
    item = next(i for i in items if i.id == "hi-t-02")  # Hindi, a table, a translated passage
    chunks = {}
    results = []
    for n, p in enumerate(item.passages):
        cid = uuid.uuid4()
        doc = SimpleNamespace(id=uuid.uuid4(), file_name=p.file_name, external_id=None)
        chunks[cid] = SimpleNamespace(
            id=cid,
            text=p.text,
            page=p.page,
            structure_path=p.section.split(" > ") if p.section else [],
            document_version=SimpleNamespace(document=doc),
        )
        results.append(ContextSearchResult(id=cid, score=1.0 - n / 10))
    assembler = ContextAssembler(settings, sanitizer=ContentSanitizer(settings))

    async def load(_session: Any, ids: list[uuid.UUID]) -> dict[uuid.UUID, Any]:
        return {i: chunks[i] for i in ids}

    assembler._load_chunks = load  # type: ignore[method-assign]
    prompt = compiled(item)
    assembled = asyncio.run(
        assembler.assemble(
            None,  # type: ignore[arg-type]
            results,
            item.question,
            system_prompt=prompt.system_prompt,
            constraints=prompt.constraints,
            preserve_order=True,
        )
    )
    ours = user_message(item, settings).content
    assert fingerprint(assembled.messages[-1].content) == fingerprint(ours)
    assert ours != fingerprint(ours)  # the sanitizer's boundary ids are there, and masked


# ---------------------------------------------------------------------------- thinking


def test_minimal_thinking_mapping() -> None:
    off = th._minimal("x/y", "off", True)
    assert off[0].body == {"reasoning": {"enabled": False}} and off[-1].body == {}
    assert th._minimal("x/y", "low", True)[0].body == {"reasoning": {"effort": "low"}}
    assert th._minimal("x/y", "provider-default", True) == [
        th.ThinkingOption("", "provider-default")
    ]
    assert th._minimal("x/y", "off", False)[0].kwargs == {"reasoning_effort": "none"}
    with pytest.raises(ValueError):
        th.check_level("max")


def test_merge_body_keeps_the_host_pin() -> None:
    kwargs = {"extra_body": {"provider": {"order": ["groq"]}}}
    merged = th.merge_body(kwargs, {"reasoning": {"enabled": False}})
    assert merged["extra_body"] == {
        "provider": {"order": ["groq"]},
        "reasoning": {"enabled": False},
    }


def test_capture_refuses_to_double_send_thinking() -> None:
    cap = capture.Capture(
        option=th.ThinkingOption("k", "off", body={"reasoning": {"enabled": False}})
    )
    with pytest.raises(capture.DoubleThinkingError):
        capture._prepare(cap, {"model": "m", "reasoning_effort": "low"})
    sent = capture._prepare(cap, {"model": "m", "api_key": "secret"})
    assert sent["extra_body"]["reasoning"] == {"enabled": False}
    assert "api_key" not in capture.safe_params(sent)


def test_harness_mode_sends_its_own_thinking_and_moves_past_a_refusal(
    tmp_path: Path, items: list[Item], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(th, "engine_handles_thinking", lambda: False)
    chosen = items[:3]
    _run(_cfg(tmp_path / "a", chosen))
    recs = _answers(tmp_path / "a")
    assert {r.thinking_source for r in recs} == {"harness"}
    assert all("reasoning" in r.request_params["extra_body"] for r in recs)
    _run(_cfg(tmp_path / "b", chosen, models="fake/rejects-reasoning"))
    recs = _answers(tmp_path / "b")
    assert len(recs) == 3 and all(
        "reasoning" not in r.request_params.get("extra_body", {}) for r in recs
    )


# ---------------------------------------------------------------------------- runner


def test_runner_persists_complete_valid_records(tmp_path: Path, items: list[Item]) -> None:
    chosen = items[:4]
    progress = _run(_cfg(tmp_path, chosen, variants=["off", "low"]))
    assert progress.status == "complete"
    recs = _answers(tmp_path)
    assert len(recs) == 8
    for r in recs:
        assert r.answer_raw and r.ttft_ms is not None and r.cost_known and r.host == "FakeHost"
        assert r.request_params["stream"] is True and r.rail_failures == []
        assert (r.tokens_reasoning or 0) == (0 if r.variant == "off" else 48)
    assert progress.models["fake/good|low"].done == 4


def test_kill_mid_run_then_resume_has_no_duplicates(tmp_path: Path, items: list[Item]) -> None:
    chosen = items[:8]
    fake = FakeModel(items, first_token_s=0.01)
    cfg = _cfg(tmp_path, chosen, inner=fake.acompletion, concurrency=1)

    async def kill_after_three() -> None:
        task = asyncio.create_task(generate.Runner(cfg).run())
        while len(_lines(tmp_path / "answers.jsonl")) < 3:
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(kill_after_three())
    with (tmp_path / "answers.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"partial": ')  # the kill also tore a write
    done_before = {r.item_id for r in _answers_tolerant(tmp_path)}
    calls_before = sum(fake.calls.values())
    progress = _run(_cfg(tmp_path, chosen, inner=fake.acompletion))
    recs = _answers(tmp_path)
    keys = [r.key for r in recs]
    assert len(keys) == len(set(keys)) == 8 and progress.status == "complete"
    # the rerun only paid for what was missing
    assert sum(fake.calls.values()) - calls_before == 8 - len(done_before)


def _lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text("utf-8").splitlines() if ln] if path.exists() else []


def _answers_tolerant(tmp: Path) -> list[AnswerRecord]:
    out = []
    for line in _lines(tmp / "answers.jsonl"):
        with contextlib.suppress(ValidationError):
            out.append(AnswerRecord.model_validate_json(line))
    return out


def test_resume_refuses_a_changed_item_set(tmp_path: Path, items: list[Item]) -> None:
    _run(_cfg(tmp_path, items[:1]))
    with pytest.raises(generate.RunAbortError):
        _run(_cfg(tmp_path, items[:1], items_sha256="other"))


def test_budget_cap_stops_before_it_is_exceeded(tmp_path: Path, items: list[Item]) -> None:
    chosen = items[:10]
    cap = 0.0007  # about three fake answers
    progress = _run(_cfg(tmp_path, chosen, budget_usd=cap, concurrency=2))
    recs = _answers(tmp_path)
    assert progress.status == "stopped" and progress.stop_reason.startswith("budget")
    assert 0 < len(recs) < 10
    assert sum(r.cost_usd or 0 for r in recs) <= cap
    assert progress.models["fake/good|off"].status == "stopped_budget"


def test_fail_fast_skips_a_broken_model_and_keeps_skipping_it(
    tmp_path: Path, items: list[Item]
) -> None:
    chosen = items[:10]
    progress = _run(_cfg(tmp_path, chosen, models="fake/empty,fake/good"))
    bad = progress.models["fake/empty|off"]
    assert bad.status == "skipped_fail_fast" and bad.done + bad.errors <= 5 + 1
    assert progress.models["fake/good|off"].status == "complete"
    again = _run(_cfg(tmp_path, chosen, models="fake/empty"))
    assert again.models["fake/empty|off"].status == "skipped_fail_fast"
    retried = _run(_cfg(tmp_path, chosen, models="fake/empty", retry_skipped=True))
    assert retried.models["fake/empty|off"].status == "skipped_fail_fast"  # fails again


def test_write_json_retries_a_briefly_locked_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.answer_bench import records

    real = records.os.replace
    calls = {"n": 0}

    def flaky(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("[WinError 5] Access is denied")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(records.os, "replace", flaky)
    target = tmp_path / "progress.json"
    records.write_json(target, {"ok": True})
    assert calls["n"] == 3 and json.loads(target.read_text("utf-8")) == {"ok": True}


def test_retry_skipped_ignores_the_old_errors_that_caused_the_skip(
    tmp_path: Path, items: list[Item]
) -> None:
    # A model skipped by transient errors (e.g. a key limit) must get a clean fail-fast window
    # on --retry-skipped, not be skipped again by its old errors after a few good answers.
    chosen = items[:10]
    first = _run(_cfg(tmp_path, chosen, models="fake/error500", max_retries=0))
    assert first.models["fake/error500|off"].status == "skipped_fail_fast"
    run_dir = next(p for p in tmp_path.rglob("progress.json")).parent
    for name in ("errors.jsonl", "answers.jsonl", "progress.json"):
        path = run_dir / name
        if path.exists():
            path.write_text(path.read_text("utf-8").replace("fake/error500", "fake/good"), "utf-8")
    retried = _run(_cfg(tmp_path, chosen, models="fake/good", retry_skipped=True))
    assert retried.models["fake/good|off"].status == "complete"
    assert retried.models["fake/good|off"].done == len(chosen)


def test_retry_skipped_also_clears_old_errors_of_an_interrupted_key(
    tmp_path: Path, items: list[Item]
) -> None:
    # A crash can leave a key that hit transient errors marked "running", not skipped.
    chosen = items[:10]
    _run(_cfg(tmp_path, chosen, models="fake/error500", max_retries=0))
    run_dir = next(p for p in tmp_path.rglob("progress.json")).parent
    for name in ("errors.jsonl", "answers.jsonl", "progress.json"):
        path = run_dir / name
        if path.exists():
            text = path.read_text("utf-8").replace("fake/error500", "fake/good")
            path.write_text(text.replace('"skipped_fail_fast"', '"running"'), "utf-8")
    retried = _run(_cfg(tmp_path, chosen, models="fake/good", retry_skipped=True))
    assert retried.models["fake/good|off"].status == "complete"


def test_thinking_off_violation_is_a_failure(tmp_path: Path, items: list[Item]) -> None:
    progress = _run(_cfg(tmp_path, items[:6], models="fake/thinks-anyway"))
    assert progress.models["fake/thinks-anyway|off"].status == "skipped_fail_fast"
    assert all("reasoning_when_off" in r.rail_failures for r in _answers(tmp_path))


def test_rate_limits_are_retried_and_counted(tmp_path: Path, items: list[Item]) -> None:
    progress = _run(_cfg(tmp_path, items[:3], models="fake/flaky"))
    recs = _answers(tmp_path)
    assert len(recs) == 3 and all(r.retries == 1 for r in recs) and progress.retries == 3


def test_server_errors_are_recorded_and_fail_fast(tmp_path: Path, items: list[Item]) -> None:
    progress = _run(_cfg(tmp_path, items[:8], models="fake/error500", max_retries=1))
    state = progress.models["fake/error500|off"]
    assert state.status == "skipped_fail_fast" and state.done == 0 and state.errors >= 2
    errors = read_jsonl(tmp_path / "errors.jsonl", AnswerRecord)
    assert errors and all(e.route == "error" and e.error_kind == "http_500" for e in errors)
    assert not (tmp_path / "answers.jsonl").exists() or not _answers(tmp_path)


# ---------------------------------------------------------------------------- scorers


def _rec(item: Item, answer: str, route: str = "grounded") -> AnswerRecord:
    return AnswerRecord.model_validate(
        _record(
            item_id=item.id,
            answer_raw=answer,
            answer_released=answer,
            route=route,
            language=item.language,
            item_type=item.type,
        )
    )


def _item(items: list[Item], item_id: str) -> Item:
    return next(i for i in items if i.id == item_id)


def test_fact_matching() -> None:
    assert score.contains("It is 8.5 hours [2].", "8.5 hours")
    assert not score.contains("It is 8.5 hours.", "8 hours")
    assert score.contains("Rs 1,800", "1800") and score.contains("429.00 dollars", "429")
    assert not score.contains("see [4]", "4") and score.contains("upto ४ canisters", "4")
    assert score.contains("5" + chr(0x2013) + "8 business days", "5-8")


def test_scorer_passes_a_good_answer(items: list[Item]) -> None:
    for item_id in ("en-a-01", "hi-a-03", "hl-a-06", "en-t-01", "hi-t-02", "en-u-01", "hl-u-04"):
        item = _item(items, item_id)
        s = score.score(item, _rec(item, answer_for(item)))
        assert s.passed, (item_id, s)


def test_scorer_catches_each_failure(items: list[Item]) -> None:
    en = _item(items, "en-a-01")
    invented = score.score(en, _rec(en, "A shift is 9 hours and costs Rs 999 [4]."))
    assert not invented.claim_check_pass and invented.claims_removed >= 1
    assert invented.forbidden_hits == ["9 hours"] and invented.gold_recall == 0.0
    bad_cite = score.score(en, _rec(en, "A full-time shift is 8.5 hours [9]."))
    assert bad_cite.citations_invalid == [9] and not bad_cite.citations_valid
    refused = score.score(en, _rec(en, "I don't have that information in our documents."))
    assert refused.refusal and not refused.refusal_correct
    hi = _item(items, "hi-a-01")
    english = score.score(hi, _rec(hi, "A full-time shift is 8.5 hours [4]."))
    assert english.reply_language == "en" and not english.language_match
    un = _item(items, "en-u-01")
    answered = score.score(un, _rec(un, "Students get 35 percent off [1]."))
    assert not answered.refusal_correct and answered.forbidden_hits
    gated = score.score(un, _rec(un, "", route="no_answer"))
    assert gated.refusal and gated.refusal_correct
    table = _item(items, "en-t-03")
    prose = score.score(
        table,
        _rec(
            table,
            "Zone A is 1-2 days for 6.95, Zone B 3-4 days for "
            "9.95 and Zone C 5-8 days for 19.95 [1].",
        ),
    )
    assert prose.table_present is False and prose.table_ok is False and not prose.passed
    long = score.score(en, _rec(en, "A shift is 8.5 hours [4]. " + "More words here. " * 60))
    assert not long.length_ok


# ---------------------------------------------------------------------------- the CLI


def test_dry_run_end_to_end(tmp_path: Path) -> None:
    code = cli.main(
        [
            "run",
            "--dry-run",
            "--out-root",
            str(tmp_path),
            "--run",
            "d",
            "--items",
            "en-a-01,en-t-03,en-u-02,hi-a-03,hi-t-02,hl-a-06,hl-u-04",
        ]
    )
    assert code == cli.EXIT_OK
    run = tmp_path / "d"
    for name in (
        "run.json",
        "answers.jsonl",
        "scores.jsonl",
        "judge.jsonl",
        "progress.json",
        "summary.json",
        "summary.md",
    ):
        assert (run / name).exists(), name
    summary = json.loads((run / "summary.json").read_text("utf-8"))
    assert {r["key"] for r in summary["rows"]} == {"fake/good|off", "fake/good|low"}


@pytest.mark.parametrize(
    ("model", "thinking", "expected"),
    [
        ("fake/good", "off", cli.EXIT_OK),
        ("fake/good", "low", cli.EXIT_OK),
        ("fake/thinks-anyway", "off", cli.EXIT_CHECKS_FAILED),
        ("fake/no-cost", "off", cli.EXIT_CHECKS_FAILED),
        ("fake/no-usage", "off", cli.EXIT_CHECKS_FAILED),
        ("fake/empty", "off", cli.EXIT_CHECKS_FAILED),
    ],
)
def test_smoke_exit_codes(tmp_path: Path, model: str, thinking: str, expected: int) -> None:
    code = cli.main(
        [
            "run",
            "--dry-run",
            "--smoke",
            "--models",
            model,
            "--thinking",
            thinking,
            "--out-root",
            str(tmp_path),
            "--run",
            "s",
        ]
    )
    assert code == expected


def test_paid_runs_need_a_budget_and_real_models(tmp_path: Path) -> None:
    base = ["run", "--out-root", str(tmp_path), "--run", "p"]
    assert cli.main([*base, "--models", "openai/gpt-4.1-mini"]) == cli.EXIT_USAGE
    assert cli.main([*base, "--models", "fake/good", "--budget-usd", "1"]) == cli.EXIT_USAGE
    assert cli.main([*base, "--dry-run", "--models", "openai/gpt-4.1-mini"]) == cli.EXIT_USAGE
    assert cli.main([*base, "--smoke", "--models", "a/b,c/d"]) == cli.EXIT_USAGE


def test_select_items_and_model_specs(items: list[Item]) -> None:
    assert [i.id for i in select_items(items, ids=["hl-u-01", "en-a-01"])] == ["hl-u-01", "en-a-01"]
    with pytest.raises(ValueError):
        select_items(items, ids=["nope"])
    spec = generate.ModelSpec.parse("qwen/qwen3-32b@cerebras+groq")
    assert (
        spec.provider_order == ("cerebras", "groq") and spec.key == "qwen/qwen3-32b@cerebras+groq"
    )
    assert [m.key for m in generate.parse_models("a/b, c/d@x")] == ["a/b", "c/d@x"]


# ---------------------------------------------------------------------------- host capture


class _Clock:
    """A fake clock: sleeping advances it, so the 60 s retry window costs no real time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def _generation_api(clock: _Clock, published_at: float | None) -> Any:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if published_at is None or clock.now < published_at:
            return httpx.Response(404, json={"error": "not found"})
        body = {"provider_name": "Groq", "total_cost": 0.0012, "native_tokens_reasoning": 0}
        return httpx.Response(200, json={"data": body})

    return httpx.MockTransport(handler)


def _or_lookup(clock: _Clock, published_at: float | None) -> Any:
    from benchmarks.answer_bench.hosts import OpenRouterLookup

    return OpenRouterLookup(
        "k",
        sleep=clock.sleep,
        clock=clock.time,
        transport=_generation_api(clock, published_at),
    )


def test_lookup_waits_through_404s_until_the_record_is_published() -> None:
    clock = _Clock()
    meta = asyncio.run(_or_lookup(clock, published_at=25.0).lookup("gen-1"))
    assert meta.host == "Groq" and meta.total_cost == 0.0012 and meta.native_tokens_reasoning == 0
    assert 25.0 <= clock.now < 60.0
    assert clock.sleeps == sorted(clock.sleeps)  # backs off, never faster


def test_lookup_gives_up_within_its_window_when_nothing_is_published() -> None:
    clock = _Clock()
    meta = asyncio.run(_or_lookup(clock, published_at=None).lookup("gen-1"))
    assert meta.empty and clock.now <= 60.0


def test_lookup_does_not_retry_a_client_error() -> None:
    import httpx
    from benchmarks.answer_bench.hosts import OpenRouterLookup

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={})

    clock = _Clock()
    lookup = OpenRouterLookup(
        "k", sleep=clock.sleep, clock=clock.time, transport=httpx.MockTransport(handler)
    )
    assert asyncio.run(lookup.lookup("g")).empty and len(calls) == 1


class _SlowLookup:
    """Looks up only after the answer is on disk and the run has moved on."""

    source = "openrouter_generation"

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.saved_without_host: list[bool] = []

    async def lookup(self, generation_id: str) -> Any:
        from benchmarks.answer_bench.hosts import GenerationMeta

        await asyncio.sleep(0.05)
        recs = [r for r in _answers_tolerant(self.run_dir) if r.generation_id == generation_id]
        self.saved_without_host.append(bool(recs) and recs[0].host is None)
        return GenerationMeta("LateHost", None, 0)


def test_late_host_is_filled_in_after_the_answer_is_saved(
    tmp_path: Path, items: list[Item]
) -> None:
    lookup = _SlowLookup(tmp_path)
    _run(_cfg(tmp_path, items[:4], lookup=lookup, concurrency=2))
    recs = _answers(tmp_path)
    assert len(recs) == 4 and {r.host for r in recs} == {"LateHost"}
    assert {r.host_source for r in recs} == {"openrouter_generation"}
    assert all(r.rail_failures == [] and "host_unknown" not in r.warnings for r in recs)
    assert lookup.saved_without_host == [True] * 4  # every answer was persisted first


def test_a_lookup_that_never_answers_does_not_fail_the_run(
    tmp_path: Path, items: list[Item]
) -> None:
    from benchmarks.answer_bench.hosts import GenerationMeta

    class Empty:
        source = "openrouter_generation"

        async def lookup(self, generation_id: str) -> GenerationMeta:
            return GenerationMeta(None, None, None)

    progress = _run(_cfg(tmp_path, items[:3], lookup=Empty()))
    assert progress.status == "complete"
    recs = _answers(tmp_path)
    assert len(recs) == 3 and all(r.host is None and "host_unknown" in r.warnings for r in recs)


def test_backfill_fills_hosts_in_order_and_is_idempotent(tmp_path: Path, items: list[Item]) -> None:
    from benchmarks.answer_bench.backfill import backfill_run
    from benchmarks.answer_bench.hosts import NoLookup

    _run(_cfg(tmp_path, items[:5], lookup=NoLookup()))
    before = _answers(tmp_path)
    assert all(r.host is None for r in before)
    result = asyncio.run(backfill_run(tmp_path, FakeLookup()))
    assert (result.updated, result.unchanged, result.unresolved) == (5, 0, 0)
    after = _answers(tmp_path)
    assert [r.key for r in after] == [r.key for r in before]  # same order
    assert {r.host for r in after} == {"FakeHost"} and all(r.host_source == "fake" for r in after)
    assert all("host_unknown" not in r.warnings for r in after)
    assert not list(tmp_path.glob("answers.jsonl.tmp"))
    snapshot = (tmp_path / "answers.jsonl").read_bytes()
    again = asyncio.run(backfill_run(tmp_path, FakeLookup()))
    assert (again.updated, again.unchanged) == (0, 5)
    assert (tmp_path / "answers.jsonl").read_bytes() == snapshot


def test_backfill_leaves_unpublished_records_alone_and_counts_them(
    tmp_path: Path, items: list[Item]
) -> None:
    from benchmarks.answer_bench.backfill import backfill_run
    from benchmarks.answer_bench.hosts import GenerationMeta, NoLookup

    class Empty:
        source = "openrouter_generation"

        async def lookup(self, generation_id: str) -> GenerationMeta:
            return GenerationMeta(None, None, None)

    _run(_cfg(tmp_path, items[:2], lookup=NoLookup()))
    snapshot = (tmp_path / "answers.jsonl").read_bytes()
    result = asyncio.run(backfill_run(tmp_path, Empty()))
    assert (result.updated, result.unresolved) == (0, 2)
    assert (tmp_path / "answers.jsonl").read_bytes() == snapshot


def test_apply_meta_surfaces_native_reasoning_and_fills_missing_cost(
    tmp_path: Path, items: list[Item]
) -> None:
    from benchmarks.answer_bench.hosts import GenerationMeta, NoLookup

    _run(_cfg(tmp_path, items[:1], lookup=NoLookup()))
    rec = _answers(tmp_path)[0]
    blind = rec.model_copy(update={"cost_usd": None, "cost_known": False, "cost_source": "unknown"})
    out = generate.apply_meta(blind, GenerationMeta("Groq", 0.003, 25), "openrouter_generation")
    assert (out.cost_usd, out.cost_known, out.cost_source) == (0.003, True, "generation_api")
    assert out.tokens_reasoning == 25 and "reasoning_when_off" in out.rail_failures
    assert out.host == "Groq"
    # a provider-reported cost is never replaced
    kept = generate.apply_meta(rec, GenerationMeta("Groq", 99.0, 0), "openrouter_generation")
    assert kept.cost_usd == rec.cost_usd and kept.host == "Groq"


# ---------------------------------------------------------------------------- budget estimate


def test_first_call_estimate_uses_a_typical_answer_not_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import callobuzz_rag.llm.cost as cost_mod

    seen: list[tuple[int, int]] = []

    def priced(model: str, provider: str, tokens_in: int, tokens_out: int) -> float:
        seen.append((tokens_in, tokens_out))
        return tokens_in * 1e-6 + tokens_out * 5e-6  # a Haiku-like price list

    monkeypatch.setattr(cost_mod, "priced_cost", priced)
    estimate = cli._estimator("openrouter")
    off = estimate("m", "off", 1500)
    think = estimate("m", "high", 1500)
    assert seen[0][1] == cli.TYPICAL_OUTPUT_TOKENS["off"] < 800
    assert off == pytest.approx((1500 * 1e-6 + 300 * 5e-6) * 1.5)
    assert off < 0.013 / 2 < 0.0075  # two concurrent first calls fit a 1.3 cent smoke budget
    assert think > off


def test_estimate_follows_finished_calls_and_the_hard_cap_still_holds(
    tmp_path: Path, items: list[Item]
) -> None:
    # first calls reserve 0.004 each (two at once = 0.008 <= cap); afterwards the estimate is
    # 1.25 x the dearest finished call (~0.0002), so the whole run fits
    cap = 0.009
    progress = _run(
        _cfg(tmp_path, items[:6], budget_usd=cap, concurrency=2, estimate=lambda m, v, p: 0.004)
    )
    recs = _answers(tmp_path)
    assert progress.status == "complete" and len(recs) == 6
    assert sum(r.cost_usd or 0 for r in recs) <= cap
    # and an estimate that cannot fit still stops the run before it spends anything
    stopped = _run(
        _cfg(tmp_path / "b", items[:2], budget_usd=0.003, estimate=lambda m, v, p: 0.004)
    )
    assert stopped.status == "stopped" and not _answers(tmp_path / "b")


# ---------------------------------------------------------------------------- gate, regate


def test_claim_check_measures_the_released_answer(items: list[Item]) -> None:
    en = _item(items, "en-a-01")
    good = "I'm Asha, the AI assistant of Summit Trail Outfitters.\n\nA shift is 8.5 hours [4]."
    kept = score.score(en, _rec(en, good))
    assert kept.claim_check_pass and kept.claims_removed == 0 and kept.gate_false_removals == 0
    lost = score.score(en, _rec(en, good).model_copy(update={"answer_released": ""}))
    assert not lost.claim_check_pass and not lost.forbidden_hits  # the gold fact never shipped


def test_false_removals_are_counted_apart_from_real_ones(items: list[Item]) -> None:
    hi = _item(items, "hi-t-01")
    assert score._supported(hi, "money", "₹14,999", "1499900")
    assert not score._supported(hi, "money", "₹15,499", "1549900")
    invented = score.claim_check(hi, "Ridge Lite 2 की कीमत ₹15,499 है [2]।")
    assert invented.removed == 1 and invented.false_removals == []


def test_regate_and_smoke_summary_rebuild_from_saved_answers(tmp_path: Path) -> None:
    base = ["--out-root", str(tmp_path), "--run", "smoke-fake"]
    assert cli.main(["run", "--dry-run", "--smoke", "--models", "fake/good", *base]) == 0
    path = tmp_path / "smoke-fake" / "answers.jsonl"
    fresh = {r.item_id: r for r in read_jsonl(path, AnswerRecord)}
    stale = [
        r.model_copy(update={"answer_released": "", "gate_removed": 9}) for r in fresh.values()
    ]
    write_jsonl(path, list(stale))
    assert cli.main(["regate", *base]) == cli.EXIT_OK
    redone = {r.item_id: r for r in read_jsonl(path, AnswerRecord)}
    for item_id, rec in fresh.items():
        assert redone[item_id].answer_released == rec.answer_released
        assert redone[item_id].gate_removed == rec.gate_removed
        assert redone[item_id].route == rec.route
        assert redone[item_id].cost_usd == rec.cost_usd  # nothing regenerated
    out = tmp_path / "summary.md"
    code = cli.main(["smoke-summary", "--out-root", str(tmp_path), "--out", str(out)])
    assert code == cli.EXIT_OK
    table = out.read_text("utf-8")
    assert "`fake/good`" in table and "**PASS**" in table and "3/3" in table


def test_regate_handles_a_length_stop_like_the_engine(items: list[Item]) -> None:
    from benchmarks.answer_bench.regate import regate_answer

    hi = _item(items, "hi-t-01")
    raw = "तुलना [2]:\n\n| नाम | कीमत (रु) |\n|---|---|\n| Ridge Lite 2 | 14,999 |\n| Solo Biv"
    rec = AnswerRecord.model_validate(
        _record(item_id=hi.id, answer_raw=raw, finish_reason="length", language="hi")
    )
    out = asyncio.run(regate_answer(hi, rec, Settings(_env_file=None)))
    assert out.truncated and out.removed == 0 and out.route == "grounded"
    assert out.released.rstrip().endswith("| Ridge Lite 2 | 14,999 |")


def test_a_first_token_that_arrived_is_never_a_zero_ttft() -> None:
    """Under half a millisecond rounds to 0, which the verifier reads as "TTFT missing": the
    dry run's fake host answers that fast, so it failed whenever one item won the race."""
    cap = capture.Capture(started=10.0, first_content_at=10.0002)
    assert cap.ttft_ms() == 1
    assert capture.Capture(started=10.0).ttft_ms() is None  # no token at all stays missing
