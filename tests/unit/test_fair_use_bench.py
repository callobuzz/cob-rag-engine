"""benchmarks/fair_use.py: the final test's harness, proven on in-process fakes (no Docker, no
network beyond 127.0.0.1, nothing paid). The real numbers come from SV's run with Docker up."""

import argparse
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

from benchmarks import fair_use as bench  # noqa: E402


def args() -> argparse.Namespace:
    return argparse.Namespace(engine_url=None, widget_key=None, origin=None, questions_file=None)


def run_all() -> list[bench.Section]:
    return asyncio.run(
        bench.run_sections(bench.DryEnv(), bench.Sizes.quick(), bench.SECTIONS, args())
    )


def test_a_dry_run_exercises_every_section() -> None:
    sections = run_all()
    assert [s.key for s in sections] == list(bench.SECTIONS)
    for s in sections:
        assert s.errors == [], (s.key, s.errors)
        assert s.skipped is None, s.key
        assert s.ok is True, (s.key, [t for t in s.targets if not t[1]])
        assert s.lines, s.key  # plain-language results were produced


def test_the_numbers_have_the_shape_the_report_and_docs_use() -> None:
    by = {s.key: s for s in run_all()}
    assert set(by["gate"].numbers["scenarios"]) == {"no limits", "4 limits", "all 12 limits"}
    for stats in by["gate"].numbers["scenarios"].values():
        assert {"p50_ms", "p95_ms", "n"} <= set(stats)
    lanes = by["lanes"].numbers
    assert lanes["slot_keys_left"] == 0 and {"high", "low"} <= set(lanes)
    throttle = by["throttle"].numbers
    assert throttle["on_plan"] == throttle["limit"]
    assert throttle["owner_mails"] == ["80", "100", "150"]
    local = by["local"].numbers
    assert local["questions"] == 20 and local["answered"] == 20  # 8 en + 6 hi + 6 hi-Latn
    assert set(local["by_language_total_p50_s"]) == {"en", "hi", "hi-Latn"}
    website = by["website"].numbers
    assert website["freshness"]["reread"] == bench.Sizes.quick().site_changed
    assert website["limits"]["resumed"] is True


def test_the_questions_are_eight_english_six_hindi_six_hinglish() -> None:
    assert {k: len(v) for k, v in bench.QUESTIONS.items()} == {"en": 8, "hi": 6, "hi-Latn": 6}


def test_a_dry_run_writes_nothing_outside_its_own_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results_before = sorted(p.name for p in bench.RESULTS.glob("*"))
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["fair_use", "--dry-run", "--out", str(out), "--label", "t"])
    assert bench.main() == bench.EXIT_OK
    assert sorted(p.name for p in out.iterdir()) == ["fair-use-t.json", "fair-use-t.md"]
    assert sorted(p.name for p in bench.RESULTS.glob("*")) == results_before
    report = json.loads((out / "fair-use-t.json").read_text(encoding="utf-8"))
    assert report["dry_run"] is True and report["integration"]["skipped"] == "dry run"
    markdown = (out / "fair-use-t.md").read_text(encoding="utf-8")
    assert "Not measured" in markdown  # a dry run's numbers are never mistaken for results


def test_a_dry_run_without_out_uses_a_temporary_folder(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    before = sorted(p.name for p in bench.RESULTS.glob("*"))
    monkeypatch.setattr(sys, "argv", ["fair_use", "--dry-run", "--label", "tmpcheck"])
    assert bench.main() == bench.EXIT_OK
    assert sorted(p.name for p in bench.RESULTS.glob("*")) == before
    assert "fair-use-bench-" in capsys.readouterr().out


def test_the_website_section_only_ever_requests_127_0_0_1(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts: list[str | None] = []
    real_get = httpx.Client.get

    def spy(self: httpx.Client, url: Any, *a: Any, **k: Any) -> httpx.Response:
        hosts.append(httpx.URL(str(url)).host)
        return real_get(self, url, *a, **k)

    monkeypatch.setattr(httpx.Client, "get", spy)
    sections = asyncio.run(
        bench.run_sections(bench.DryEnv(), bench.Sizes.quick(), ["website"], args())
    )
    assert sections[0].ok is True
    assert hosts and set(hosts) == {"127.0.0.1"}


def test_anything_else_is_refused_before_a_request_is_made() -> None:
    assert bench.assert_local("http://127.0.0.1:8123/x") == "http://127.0.0.1:8123/x"
    for url in ("https://shreedental.in/", "http://10.0.0.5/", "http://example.com:80/a"):
        with pytest.raises(bench.NotLocalError):
            bench.assert_local(url)


def test_the_fixture_site_answers_conditional_requests_and_has_a_sitemap_with_lastmod() -> None:
    site = bench.FixtureSite(5)
    try:
        with httpx.Client(trust_env=False) as client:
            sitemap = client.get(site.base + "/sitemap.xml")
            assert sitemap.text.count("<lastmod>") == 5
            first = client.get(site.base + "/p/2")
            again = client.get(site.base + "/p/2", headers={"If-None-Match": first.headers["etag"]})
            assert (first.status_code, again.status_code) == (200, 304)
            site.change(1, start=2)
            changed = client.get(
                site.base + "/p/2", headers={"If-None-Match": first.headers["etag"]}
            )
            assert changed.status_code == 200
    finally:
        site.stop()


def test_percentiles() -> None:
    assert bench.percentile([], 50) is None
    assert bench.percentile([4.0], 95) == 4.0
    assert bench.percentile([1, 2, 3, 4, 5], 50) == 3
    assert bench.percentile([0, 10], 95) == pytest.approx(9.5)
    stats = bench.stats_ms([0.001, 0.002, 0.003])
    assert stats["p50_ms"] == 2.0 and stats["n"] == 3


def test_a_missed_target_is_reported_in_words_and_fails_the_section() -> None:
    s = bench.Section("gate", "Gate overhead")
    s.target("4 limits add at most 5 ms", False)
    assert s.finish().ok is False
    report = bench.build_report(
        [s], label="t", dry=False, integration={"skipped": "x"}, done_check=None
    )
    markdown = bench.render_markdown(report)
    assert "| 4 limits add at most 5 ms | missed |" in markdown and report["all_met"] is False


def test_the_fake_redis_behaves_like_the_slot_keys_need() -> None:
    async def go() -> None:
        r = bench.FakeRedis()
        assert await r.set("k", "a", nx=True, ex=5) is True
        assert await r.set("k", "b", nx=True, ex=5) is None  # taken
        assert await r.eval(bench_release(), 1, "k", "wrong") == 0  # not ours
        assert await r.eval(bench_release(), 1, "k", "a") == 1
        assert await r.get("k") is None

    asyncio.run(go())


def bench_release() -> str:
    from callobuzz_rag.tiers.lanes import RELEASE_SCRIPT

    return RELEASE_SCRIPT


def test_the_integration_tests_it_re_runs_exist() -> None:
    for path in bench.INTEGRATION_TESTS:
        assert (ROOT / path).exists(), path


def test_unknown_sections_are_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["fair_use", "--dry-run", "--sections", "gate,nonsense"])
    assert bench.main() == bench.EXIT_USAGE


def test_the_local_section_is_skipped_with_a_reason_when_no_stack_is_given() -> None:
    class NoStack(bench.DryEnv):
        def local_client(
            self, a: argparse.Namespace
        ) -> tuple[httpx.AsyncClient | None, str | None]:
            return None, "needs --engine-url, --widget-key and --origin"

    sections = asyncio.run(bench.run_sections(NoStack(), bench.Sizes.quick(), ["local"], args()))
    assert sections[0].skipped and sections[0].ok is None
