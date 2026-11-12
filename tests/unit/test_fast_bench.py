"""benchmarks/fast_models.py: the fast-model (turn-label) harness, proven with the fake model (no
paid calls): record rails, thinking verification, resume, budget cap, fail-fast, retries, the
key-limit abort, host backfill, scoring from saved labels, the merge, and the smoke gate."""

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import fast_models as cli  # noqa: E402
from benchmarks.answer_bench.generate import RunAbortError, parse_models  # noqa: E402
from benchmarks.answer_bench.hosts import GenerationMeta  # noqa: E402
from benchmarks.fast_bench import fake, records, run, score, verify  # noqa: E402

from callobuzz_rag.core.benchmark_results import FAST_MODELS_PATH  # noqa: E402
from callobuzz_rag.core.config import Settings  # noqa: E402

N = 12  # model-decided turns per test: enough for the first-five window, still fast


@pytest.fixture(scope="module")
def rows() -> list[dict[str, Any]]:
    return score.load_rows()


@pytest.fixture(scope="module")
def ids(rows: list[dict[str, Any]]) -> list[str]:
    return score.model_turn_ids(rows)


def _cfg(
    tmp: Path, rows: list[dict[str, Any]], ids: list[str], models: str = "fake/good", **kw: Any
) -> run.RunConfig:
    model = fake.FakeLabelModel(rows)
    defaults: dict[str, Any] = {
        "run": tmp.name,
        "out_dir": tmp,
        "models": parse_models(models),
        "rows": rows,
        "turn_ids": ids[:N],
        "businesses": score.load_businesses(),
        "labels_sha256": "test-sha",
        "settings": Settings(_env_file=None),
        "api_key": "dry-run",
        "provider": "openrouter",
        "budget_usd": 1.0,
        "backoff_s": 0.001,
        "dry_run": True,
        "inner": model.acompletion,
        "lookup": fake.FakeLookup(),
        "estimate": lambda m, p: fake.fake_cost(p, 100),
        "log": lambda m: None,
    }
    defaults.update(kw)
    return run.RunConfig(**defaults)


def _go(cfg: run.RunConfig) -> records.Progress:
    return asyncio.run(run.Runner(cfg).run())


def _labels(tmp: Path) -> list[records.LabelRecord]:
    return records.read_jsonl(tmp / "labels.jsonl", records.LabelRecord)


def _status(progress: records.Progress, model: str) -> str:
    return progress.models[f"{model}|off"].status


# ---------------------------------------------------------------------------- records and rails


def _rec(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "reply_raw": '{"label": "smalltalk"}',
        "fallback_reason": None,
        "tokens_in": 1200,
        "tokens_out": 30,
        "tokens_reasoning": 0,
        "cost_usd": 0.0002,
        "thinking_effective": "off",
        "thinking_sent": True,
        "thinking_minimum_only": False,
        "reasoning_text": False,
        "leaked": [],
        "host": "Groq",
        "host_source": "openrouter_generation",
        "provider_order": ["groq"],
        "finish_reason": "stop",
    }
    base.update(over)
    return base


def test_a_clean_call_has_no_rail_failures() -> None:
    assert records.rail_checks(_rec()) == ([], [])


@pytest.mark.parametrize(
    ("over", "rail"),
    [
        ({"tokens_reasoning": 7}, records.RAIL_REASONING),
        ({"tokens_reasoning": None}, records.RAIL_REASONING),
        ({"leaked": ["<think>"]}, records.RAIL_REASONING),
        ({"reasoning_text": True}, records.RAIL_REASONING),
        ({"tokens_in": None}, records.RAIL_TOKENS),
        ({"cost_usd": None}, records.RAIL_COST),
        ({"reply_raw": "  "}, records.RAIL_EMPTY),
    ],
)
def test_each_rail_can_fail_a_call(over: dict[str, Any], rail: str) -> None:
    assert rail in records.rail_checks(_rec(**over))[0]


def test_a_model_that_cannot_be_off_passes_at_its_minimum_with_reasoning_tokens() -> None:
    minimum = _rec(thinking_effective="low", thinking_minimum_only=True, tokens_reasoning=60)
    assert records.rail_checks(minimum)[0] == []
    # but not when it merely drifted to a level nobody asked for
    drifted = _rec(thinking_effective="low", thinking_minimum_only=False, tokens_reasoning=60)
    assert records.RAIL_REASONING in records.rail_checks(drifted)[0]


def test_a_timeout_fallback_is_not_a_rail_failure() -> None:
    timeout = _rec(
        reply_raw="", fallback_reason="timeout", tokens_in=None, tokens_out=None,
        tokens_reasoning=None, cost_usd=None,
    )  # fmt: skip
    assert records.rail_checks(timeout)[0] == []


def test_a_host_other_than_the_pinned_one_is_a_warning() -> None:
    assert records.WARN_PIN in records.rail_checks(_rec(host="Together"))[1]
    assert records.WARN_HOST in records.rail_checks(_rec(host=None))[1]


def test_a_record_with_a_missing_or_extra_field_is_refused(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    _go(_cfg(tmp_path, rows, ids, turn_ids=ids[:1]))
    data = _labels(tmp_path)[0].model_dump()
    del data["ms"]
    with pytest.raises(ValidationError):
        records.LabelRecord.model_validate(data)
    with pytest.raises(ValidationError):
        records.LabelRecord.model_validate({**_labels(tmp_path)[0].model_dump(), "surprise": 1})


def test_an_error_record_needs_source_error() -> None:
    with pytest.raises(ValidationError):
        records.LabelRecord.model_validate({**_full(), "error": "boom"})


def _full() -> dict[str, Any]:
    # the shape of a completed record, used to check the validator
    return {
        "harness_version": "x", "run": "r", "timestamp": "t", "model": "m", "provider": "p",
        "provider_order": [], "host": None, "host_source": "unknown", "generation_id": None,
        "turn_id": "t1", "language": "en", "business": "b", "source": "model",
        "fallback_reason": None, "label": "smalltalk", "label_language": None, "reply_raw": "{}",
        "ms": 5, "tokens_in": 1, "tokens_out": 1, "tokens_reasoning": 0, "tokens_cached": 0,
        "cost_usd": 0.1, "cost_known": True, "cost_source": "provider_usage",
        "finish_reason": "stop", "thinking_requested": "off", "thinking_effective": "off",
        "thinking_sent": True, "thinking_minimum_only": False, "reasoning_text": False,
        "leaked": [], "error": None, "error_kind": None, "retries": 0, "request_params": {},
        "request_params_hash": "h", "rail_failures": [], "warnings": [],
    }  # fmt: skip


# ---------------------------------------------------------------------------- small helpers


def test_inline_reasoning_ignores_an_empty_block() -> None:
    assert run.inline_reasoning("<think>weighing it</think>{}") is True
    assert run.inline_reasoning("<|channel>thought<channel|>{}") is False
    assert run.inline_reasoning("<think></think>{}") is False
    assert run.inline_reasoning('{"label": "other"}') is False


class _StatusError(Exception):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status_code = status


@pytest.mark.parametrize(
    ("status", "text", "fatal"),
    [
        (403, "Key limit exceeded (daily limit)", True),
        (403, "Insufficient credit", True),
        (402, "Payment required", True),
        (401, "No auth credentials found", True),
        (403, "Input was flagged by moderation", False),
        (429, "slow down", False),
        (500, "upstream", False),
    ],
)
def test_only_a_key_or_credit_limit_aborts_the_run(status: int, text: str, fatal: bool) -> None:
    assert (run.fatal_reason(_StatusError(status, text)) is not None) is fatal


def test_the_smoke_turns_cover_every_language(rows: list[dict[str, Any]], ids: list[str]) -> None:
    chosen = cli.smoke_turn_ids(rows, ids)
    by_id = {r["id"]: r for r in rows}
    assert len(chosen) == cli.SMOKE_TURNS == len(set(chosen))
    assert {by_id[t]["language"] for t in chosen} == {"en", "hi", "hi-Latn"}
    assert set(chosen) <= set(ids)


def test_the_candidate_list_is_what_sv_approved() -> None:
    assert len(cli.CANDIDATES) == 15
    assert "openai/gpt-oss-120b" in cli.CANDIDATES
    assert len(set(cli.CANDIDATES)) == 15


# ---------------------------------------------------------------------------- the runner


def test_a_good_model_is_captured_in_full_and_scored(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    progress = _go(_cfg(tmp_path, rows, ids))
    assert progress.status == "complete"
    assert _status(progress, "fake/good") == "complete"
    recs = _labels(tmp_path)
    assert len(recs) == N
    for rec in recs:
        assert verify.record_problems(rec, require_host=False) == []
    assert (tmp_path / "run.json").exists() and (tmp_path / "progress.json").exists()
    summaries = score.score_run(tmp_path, rows, cli.score.load_businesses(), 84.0, turn_ids=ids[:N])
    s = summaries["fake/good"]
    assert s["status"] == "complete" and s["thinking_control_verified"] is True
    assert s["usd_per_1000_labels"] > 0 and s["latency_s"]["p50"] is not None
    assert s["cached_prompt_share"] > 0 and s["reasoning_tokens"] == 0


def test_a_rerun_skips_every_finished_turn(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    cfg = _cfg(tmp_path, rows, ids)
    _go(cfg)
    calls = sum(cfg.inner.__self__.calls.values())  # type: ignore[union-attr]
    _go(cfg)
    assert sum(cfg.inner.__self__.calls.values()) == calls  # type: ignore[union-attr]
    assert len(_labels(tmp_path)) == N


def test_a_resume_after_a_kill_finishes_only_the_missing_turns(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    _go(_cfg(tmp_path, rows, ids, turn_ids=ids[:5]))
    cfg = _cfg(tmp_path, rows, ids)
    _go(cfg)
    assert len(_labels(tmp_path)) == N
    assert sum(cfg.inner.__self__.calls.values()) == N - 5  # type: ignore[union-attr]


def test_a_partial_last_line_is_repaired_on_resume(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    _go(_cfg(tmp_path, rows, ids, turn_ids=ids[:3]))
    path = tmp_path / "labels.jsonl"
    path.write_text(path.read_text("utf-8") + '{"harness_version": "1', "utf-8")
    _go(_cfg(tmp_path, rows, ids))
    assert len(_labels(tmp_path)) == N


def test_a_changed_turn_set_refuses_to_resume(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    _go(_cfg(tmp_path, rows, ids, turn_ids=ids[:2]))
    with pytest.raises(RunAbortError, match="turn set changed"):
        _go(_cfg(tmp_path, rows, ids, labels_sha256="other"))


def test_thinking_when_off_fails_the_model_fast(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    progress = _go(_cfg(tmp_path, rows, ids, models="fake/thinks-anyway,fake/good"))
    assert _status(progress, "fake/thinks-anyway") == "skipped_fail_fast"
    assert _status(progress, "fake/good") == "complete"
    bad = [r for r in _labels(tmp_path) if r.model == "fake/thinks-anyway"]
    assert bad and all(records.RAIL_REASONING in r.rail_failures for r in bad)
    summaries = score.score_run(tmp_path, rows, score.load_businesses(), 84.0, turn_ids=ids[:N])
    assert summaries["fake/thinks-anyway"]["thinking_control_verified"] is False
    assert summaries["fake/thinks-anyway"]["status"] == "incomplete"


def test_a_model_that_cannot_label_is_skipped_and_stays_skipped(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    cfg = _cfg(tmp_path, rows, ids, models="fake/garbage")
    assert _status(_go(cfg), "fake/garbage") == "skipped_fail_fast"
    calls = sum(cfg.inner.__self__.calls.values())  # type: ignore[union-attr]
    assert _status(_go(cfg), "fake/garbage") == "skipped_fail_fast"
    assert sum(cfg.inner.__self__.calls.values()) == calls  # type: ignore[union-attr]
    cfg.retry_skipped = True
    _go(cfg)  # a clean window: it is tried again
    assert sum(cfg.inner.__self__.calls.values()) > calls  # type: ignore[union-attr]


def test_http_500s_are_errors_not_labels(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    progress = _go(_cfg(tmp_path, rows, ids, models="fake/error500", max_retries=1))
    assert _status(progress, "fake/error500") == "skipped_fail_fast"
    assert not _labels(tmp_path)
    errors = records.read_jsonl(tmp_path / "errors.jsonl", records.LabelRecord)
    assert errors and all(e.source == "error" and e.error_kind == "http_500" for e in errors)


def test_429s_are_retried_and_recorded(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    progress = _go(_cfg(tmp_path, rows, ids, models="fake/flaky", turn_ids=ids[:60]))
    assert _status(progress, "fake/flaky") == "complete"
    recs = _labels(tmp_path)
    assert len(recs) == 60 and any(r.retries for r in recs) and progress.retries > 0
    assert not (tmp_path / "errors.jsonl").exists() or not records.read_jsonl(
        tmp_path / "errors.jsonl", records.LabelRecord
    )


def test_the_retry_cap_stops_the_run(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    progress = _go(
        _cfg(tmp_path, rows, ids, models="fake/flaky", turn_ids=ids[:60], max_total_retries=1)
    )
    assert progress.status == "stopped" and "retry cap" in (progress.stop_reason or "")


def test_the_budget_cap_stops_before_it_is_passed(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    one_call = fake.fake_cost(1500, 100)
    progress = _go(_cfg(tmp_path, rows, ids, budget_usd=one_call * 3, concurrency=1))
    assert progress.status == "stopped" and (progress.stop_reason or "").startswith("budget")
    assert 0 < len(_labels(tmp_path)) < N
    assert progress.spend_usd <= one_call * 3
    assert _status(progress, "fake/good") == "stopped_budget"


def test_a_key_limit_aborts_the_run_without_retries(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    cfg = _cfg(tmp_path, rows, ids, models="fake/key-limit,fake/good")
    with pytest.raises(RunAbortError, match="403"):
        _go(cfg)
    progress = records.Progress.model_validate_json((tmp_path / "progress.json").read_text("utf-8"))
    assert progress.status == "stopped" and progress.retries == 0
    assert not [r for r in _labels(tmp_path) if r.model == "fake/good"]  # nothing else was paid


def test_a_missing_cost_is_a_rail_failure(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    progress = _go(_cfg(tmp_path, rows, ids, models="fake/no-cost"))
    assert _status(progress, "fake/no-cost") == "skipped_fail_fast"
    assert all(records.RAIL_COST in r.rail_failures for r in _labels(tmp_path))


def test_progress_is_live_and_validated(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    seen: list[int] = []

    def log(msg: str) -> None:
        path = tmp_path / "progress.json"
        if msg.lstrip().startswith("ok") and path.exists():
            seen.append(
                records.Progress.model_validate_json(path.read_text("utf-8"))
                .models["fake/good|off"]
                .done
            )

    _go(_cfg(tmp_path, rows, ids, log=log, concurrency=1))
    assert seen == sorted(seen) and seen[-1] >= N - 1


# ---------------------------------------------------------------------------- hosts and scoring


def test_backfill_fills_host_and_cost_and_rederives_rails(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    cfg = _cfg(tmp_path, rows, ids, models="fake/no-cost", lookup=fake.FakeLookup())

    class Lookup:
        source = "openrouter_generation"

        async def lookup(self, generation_id: str) -> GenerationMeta:
            return GenerationMeta("Groq", 0.0003, 0)

    _go(cfg)
    # the run's records are errors-free but failed the cost rail; the backfill supplies the cost
    result = asyncio.run(run.backfill_run(tmp_path, Lookup()))
    assert result.updated == len(_labels(tmp_path)) and result.unresolved == 0
    for rec in _labels(tmp_path):
        assert rec.host == "Groq" and rec.cost_known and rec.cost_source == "generation_api"
        assert records.RAIL_COST not in rec.rail_failures


def test_a_label_is_rederived_from_the_saved_reply(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    from benchmarks.labels_dev import build_config

    _go(_cfg(tmp_path, rows, ids, turn_ids=ids[:3]))
    rec = _labels(tmp_path)[0]
    config = build_config(score.load_businesses()[rec.business])
    assert score.rederive(rec, config) == (rec.label, "model")
    broken = rec.model_copy(update={"reply_raw": "not json at all"})
    assert score.rederive(broken, config) == ("business_question", "fallback")
    thinking = rec.model_copy(update={"reply_raw": "<think>hm</think>" + rec.reply_raw})
    assert score.rederive(thinking, config) == (rec.label, "model")


def test_scoring_needs_every_asked_turn_or_the_model_is_incomplete(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    _go(_cfg(tmp_path, rows, ids, turn_ids=ids[:5]))
    s = score.score_run(tmp_path, rows, score.load_businesses(), 84.0, turn_ids=ids[:N])
    assert s["fake/good"]["status"] == "incomplete"
    assert len(s["fake/good"]["missing_turns"]) == N - 5


def test_scoring_uses_the_rules_for_the_turns_they_decide_and_counts_all_266(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    # the fake good model answers every turn with its gold label, so the whole set is right
    _go(_cfg(tmp_path, rows, ids, turn_ids=ids))
    s = score.score_run(tmp_path, rows, score.load_businesses(), 84.0)["fake/good"]
    assert s["turns"] == 266 and s["model_turns"] == len(ids) == 162
    assert s["status"] == "complete" and s["overall"] == 1.0 and s["passes"] is True
    lines = (tmp_path / "scores.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == 266
    assert {json.loads(x)["source"] for x in lines} == {"rules", "model"}


def test_the_percentiles_are_in_seconds() -> None:
    assert score.pct([1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000], 0.5) == 6.0
    assert score.pct([], 0.5) is None


# ---------------------------------------------------------------------------- merge and CLI


def test_only_complete_models_are_merged_and_the_file_stays_normalised(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    _go(_cfg(tmp_path, rows, ids, models="fake/good,fake/garbage", turn_ids=ids))
    summaries = score.score_run(tmp_path, rows, score.load_businesses(), 84.0)
    target = tmp_path / "fast-models.json"
    shutil.copyfile(FAST_MODELS_PATH, target)
    doc = score.merge(summaries, "2026-10-03", "fast/x/summary.md", target, replace_all=True)
    assert [m["key"] for m in doc["models"]] == ["fake/good"]
    assert doc["recommendations"]["default"] == "fake/good"
    row = doc["models"][0]
    assert row["thinking_control_verified"] is True and row["model_turns"] == 162
    from callobuzz_rag.core.benchmark_results import FastModelBenchmarks, normalise

    FastModelBenchmarks.model_validate(doc)
    assert json.loads(target.read_text("utf-8")) == normalise(doc)


def test_the_dry_run_command_verifies_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["run", "--dry-run", "--run", "dry", "--out-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK, out[-2000:]
    assert "VERIFIED" in out


def test_smoke_with_a_fake_model_is_refused_and_a_real_one_needs_a_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["run", "--models", "fake/good", "--smoke"]) == cli.EXIT_USAGE
    assert cli.main(["run", "--models", "openai/gpt-4.1-mini"]) == cli.EXIT_USAGE
    assert cli.main(["run", "--dry-run", "--models", "openai/gpt-4.1-mini"]) == cli.EXIT_USAGE
    capsys.readouterr()


def test_the_smoke_gate_fails_on_any_gap(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    chosen = cli.smoke_turn_ids(rows, ids)
    cfg = _cfg(tmp_path, rows, ids, turn_ids=chosen)
    _go(cfg)
    asyncio.run(run.backfill_run(tmp_path, cfg.lookup))
    score.score_run(tmp_path, rows, score.load_businesses(), 84.0, turn_ids=chosen)
    assert verify.verify_smoke(tmp_path, "fake/good", chosen, require_host=True) == []
    # a turn that was not captured, and a host that is unknown, are gaps
    assert verify.verify_smoke(tmp_path, "fake/good", [*chosen, "zz-999"], require_host=True)
    path = tmp_path / "labels.jsonl"
    lines = [json.loads(x) for x in path.read_text("utf-8").splitlines()]
    lines[0]["host"] = None
    lines[1]["cost_known"], lines[1]["cost_usd"] = False, None
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", "utf-8")
    problems = verify.verify_smoke(tmp_path, "fake/good", chosen, require_host=True)
    assert any("host unknown" in p for p in problems)
    assert any("cost not known" in p for p in problems)


def test_retry_skipped_gives_a_clean_fail_fast_window(
    rows: list[dict[str, Any]], ids: list[str], tmp_path: Path
) -> None:
    cfg = _cfg(tmp_path, rows, ids, models="fake/garbage")
    assert _status(_go(cfg), "fake/garbage") == "skipped_fail_fast"
    broken = len(_labels(tmp_path))
    assert broken >= 2
    healthy = fake.FakeLabelModel(rows)

    async def now_good(**kwargs: Any) -> Any:
        return await healthy.acompletion(**{**kwargs, "model": "fake/good"})

    retry = _cfg(tmp_path, rows, ids, models="fake/garbage", inner=now_good, retry_skipped=True)
    progress = _go(retry)
    # the earlier broken turns do not count against it: it now finishes
    assert _status(progress, "fake/garbage") == "complete"
    assert len(_labels(tmp_path)) == N
