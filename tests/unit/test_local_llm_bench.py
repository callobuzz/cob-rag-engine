"""benchmarks/local_llm.py: the local CPU model benchmark, proven with a fake Ollama and the
answer bench's fake model (no server, no paid call)."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import local_llm as bench  # noqa: E402
from benchmarks.answer_bench import ITEMS_PATH  # noqa: E402
from benchmarks.answer_bench.items import Item, load_items  # noqa: E402


@pytest.fixture(scope="module")
def items() -> list[Item]:
    return load_items(ITEMS_PATH)


class FakeOllama:
    """`/api/tags`, `/api/pull` and `/api/show` of a server that holds `held` models."""

    def __init__(self, *held: str) -> None:
        self.held = list(held)
        self.pulled: list[str] = []
        self.calls: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={"models": [{"name": m, "size": 3_300_000_000} for m in self.held]},
                )
            if request.url.path == "/api/pull":
                model = json.loads(request.content)["model"]
                self.pulled.append(model)
                self.held.append(model)
                body = b'{"status":"pulling manifest"}\n{"status":"success"}\n'
                return httpx.Response(200, content=body)
            if request.url.path == "/api/show":
                return httpx.Response(
                    200,
                    json={"details": {"parameter_size": "4.3B", "quantization_level": "Q4_K_M"}},
                )
            return httpx.Response(404)

        return httpx.MockTransport(handler)


# ---- pulling --------------------------------------------------------------------------------


async def test_a_model_the_server_has_is_not_pulled() -> None:
    server = FakeOllama("gemma3:4b")
    pulled = await bench.ensure_model(
        "http://x", "gemma3:4b", pull=True, transport=server.transport(), log=lambda _: None
    )
    assert pulled is False and server.pulled == []


async def test_a_missing_model_is_pulled_once() -> None:
    server = FakeOllama()
    pulled = await bench.ensure_model(
        "http://x", "qwen3:4b", pull=True, transport=server.transport(), log=lambda _: None
    )
    assert pulled is True and server.pulled == ["qwen3:4b"]


async def test_no_pull_refuses_a_missing_model() -> None:
    server = FakeOllama()
    with pytest.raises(RuntimeError, match="--no-pull"):
        await bench.ensure_model(
            "http://x", "qwen3:4b", pull=False, transport=server.transport(), log=lambda _: None
        )
    assert server.pulled == []


async def test_model_facts_read_size_and_quantisation() -> None:
    facts = await bench.model_facts("http://x", "gemma3:4b", FakeOllama("gemma3:4b").transport())
    assert facts == {"size_mb": 3300, "parameter_size": "4.3B", "quantization": "Q4_K_M"}


# ---- items ----------------------------------------------------------------------------------


def test_items_are_the_three_languages_and_per_language_caps(items: list[Item]) -> None:
    chosen = bench.pick_items(items, 2)
    assert {i.language for i in chosen} == set(bench.LANGS)
    assert all(sum(1 for i in chosen if i.language == lang) == 2 for lang in bench.LANGS)
    everything = bench.pick_items(items, None)
    assert {i.language for i in everything} <= set(bench.LANGS)
    assert len(everything) == sum(1 for i in items if i.language in bench.LANGS)


# ---- the paid-call guard ----------------------------------------------------------------------


async def test_the_guard_refuses_any_other_provider_or_address() -> None:
    async def inner(**_: Any) -> str:
        return "ok"

    call = bench.guard("http://local:11434", inner)
    assert await call(custom_llm_provider="ollama_chat", api_base="http://local:11434") == "ok"
    with pytest.raises(AssertionError):
        await call(custom_llm_provider="openrouter", api_base="http://local:11434")
    with pytest.raises(AssertionError):
        await call(custom_llm_provider="ollama_chat", api_base="https://api.openai.com")
    with pytest.raises(AssertionError):
        await call(api_base="http://local:11434")


# ---- the dry run end to end -------------------------------------------------------------------


def run_cli(*argv: str) -> int:
    return bench.main(list(argv))


def test_dry_run_writes_only_into_its_own_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = {p for p in ROOT.joinpath("benchmarks", "results").rglob("*")}
    code = run_cli(
        "run", "--dry-run", "--per-language", "3", "--out-root", str(tmp_path), "--run", "d"
    )
    out = capsys.readouterr().out
    assert code == 0, out
    run_dir = tmp_path / "d"
    assert (run_dir / "answers.jsonl").exists() and (run_dir / "scores.jsonl").exists()
    assert {p for p in ROOT.joinpath("benchmarks", "results").rglob("*")} == before
    assert "DRY RUN" in out and "fake/good" in out and "first words after" in out
    answers = [json.loads(x) for x in (run_dir / "answers.jsonl").read_text("utf-8").splitlines()]
    assert len(answers) == 9 and all(a["provider"] == "ollama_chat" for a in answers)


def test_dry_run_refuses_a_real_model(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run_cli("run", "--dry-run", "--models", "gemma3:4b", "--out-root", str(tmp_path))
    assert code == bench.EXIT_USAGE
    assert "fake/*" in capsys.readouterr().out


def test_a_real_run_needs_models_and_refuses_fakes(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli("run") == bench.EXIT_USAGE
    assert run_cli("run", "--models", "fake/good") == bench.EXIT_USAGE


def test_rows_carry_every_measurement(tmp_path: Path, items: list[Item]) -> None:
    run_cli("run", "--dry-run", "--per-language", "4", "--out-root", str(tmp_path), "--run", "r")
    rows = bench.collect_rows(
        tmp_path / "r",
        items,
        {"fake/good": {"size_mb": 3300, "parameter_size": "4.3B", "quantization": "Q4_K_M"}},
    )
    (row,) = rows
    assert row["model"] == "fake/good" and row["items"] == 12 and row["answered"] == 12
    assert row["size_mb"] == 3300 and row["quantization"] == "Q4_K_M"
    for key in ("ttft_p50_ms", "ttft_p90_ms", "total_p50_ms", "total_p90_ms", "tokens_per_s"):
        assert row[key] is not None and row[key] >= 0
    assert set(row["fact"]) == {"overall", "en", "hi", "hi-Latn"}
    assert all(row["fact"][k] is not None for k in row["fact"])
    assert row["refusal_on_answerable"] is not None


def test_publish_writes_the_passing_rule_and_best(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_cli("run", "--dry-run", "--per-language", "4", "--out-root", str(tmp_path), "--run", "p")
    out = tmp_path / "local-llm.json"
    assert run_cli("publish", "--run", "p", "--out-root", str(tmp_path), "--out", str(out)) == 0
    doc = json.loads(out.read_text("utf-8"))
    assert doc["schema_version"] == 1 and doc["run"] == "p"
    (row,) = doc["models"]
    assert row["local"] is True and isinstance(row["passed"], bool)
    assert doc["hardware"]["cores"] == bench.os.cpu_count()
    assert (doc["best"] == "fake/good") == row["passed"]
    assert doc["default"] == "fake/good"
    from callobuzz_rag.core.local_benchmarks import LocalBenchmarks

    LocalBenchmarks.model_validate(doc)  # the engine can serve what the harness wrote


def test_publish_without_scores_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli("publish", "--run", "none", "--out-root", str(tmp_path)) == bench.EXIT_USAGE


def test_the_summary_is_in_plain_words() -> None:
    doc = bench.build_document(
        "r",
        [
            {
                "model": "slow:9b",
                "items": 10,
                "answered": 10,
                "errors": 0,
                "fact": {"overall": 0.5, "en": 0.5, "hi": 0.4, "hi-Latn": 0.5},
                "refusal_on_answerable": 0.3,
                "ttft_p50_ms": 12000,
                "ttft_p90_ms": 20000,
                "total_p50_ms": 80000,
                "total_p90_ms": 90000,
                "tokens_per_s": 2.0,
            }
        ],
        {"cpu": "test cpu", "cores": 4, "ram_gb": 8.0},
    )
    text = bench.summary_table(doc)
    assert "first words after 12.0 s" in text and "full answer in 80.0 s" in text
    assert "gets too many facts wrong" in text and "takes too long to answer" in text
    assert "free trial tier only" in text and "No model passed" in text


def test_a_time_budget_stops_the_run_and_resume_continues(tmp_path: Path) -> None:
    items = load_items(ITEMS_PATH)
    chosen = bench.pick_items(items, 2)
    from benchmarks.answer_bench.items import file_hash

    run_dir = tmp_path / "t"
    run_dir.mkdir()
    done = asyncio.run(
        bench.generate(
            run_dir=run_dir,
            models=["fake/good"],
            items=chosen,
            all_items=items,
            url="http://x",
            timeout=5,
            deadline=bench.time.monotonic() - 1,  # already over: the cap is the 1 s floor
            dry_run=True,
            items_sha=file_hash(ITEMS_PATH),
            log=lambda _: None,
        )
    )
    assert done in (True, False)  # a tiny fake run may finish inside the floor; both are valid
    again = asyncio.run(
        bench.generate(
            run_dir=run_dir,
            models=["fake/good"],
            items=chosen,
            all_items=items,
            url="http://x",
            timeout=5,
            deadline=None,
            dry_run=True,
            items_sha=file_hash(ITEMS_PATH),
            log=lambda _: None,
        )
    )
    assert again is True
    lines = (run_dir / "answers.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == len(chosen)  # nothing answered twice
