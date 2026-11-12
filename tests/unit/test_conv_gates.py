# tests/unit/test_conv_gates.py
"""C1 gate 1: below the knowledge base's search-confidence threshold there is no
documented answer, whatever the model would say."""

import importlib.util
import json
from pathlib import Path

import pytest

from callobuzz_rag.conversation import gates
from callobuzz_rag.conversation.gates import retrieval_gate, threshold_for, top_dense_score
from callobuzz_rag.core.config import Settings
from callobuzz_rag.retrieval.models import ChunkScoreBreakdown
from tests.unit.conversation_helpers import make_config


def _bd(dense: float | None, rank: int = 1) -> ChunkScoreBreakdown:
    return ChunkScoreBreakdown(fused_score=1.0, pipeline_rank=rank, dense_score=dense)


def test_top_dense_score_ignores_keyword_only_chunks() -> None:
    assert top_dense_score({"a": _bd(0.41), "b": _bd(None, 2), "c": _bd(0.63, 3)}) == 0.63
    assert top_dense_score({"a": _bd(None)}) is None
    assert top_dense_score({}) is None


class TestThreshold:
    def test_corpus_value_wins(self) -> None:
        cfg = make_config(scope={"min_retrieval_score": 0.5})
        assert threshold_for(cfg, {"model": "BAAI/bge-m3"}, Settings(_env_file=None)) == (
            0.5,
            "corpus",
        )

    def test_measured_default_for_the_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            gates, "load_gate_defaults", lambda: {"BAAI/bge-m3": {"threshold": 0.47}}
        )
        assert threshold_for(make_config(), {"model": "BAAI/bge-m3"}, Settings(_env_file=None)) == (
            0.47,
            "measured",
        )

    def test_setting_then_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gates, "load_gate_defaults", lambda: {})
        s = Settings(_env_file=None, assistant_min_retrieval_score=0.3)
        assert threshold_for(make_config(), {"model": "x"}, s) == (0.3, "setting")
        assert threshold_for(make_config(), None, Settings(_env_file=None)) == (None, "none")


class TestGate:
    def test_passes_at_or_above(self) -> None:
        r = retrieval_gate(
            score_breakdown={"a": _bd(0.55)}, chunks=1, threshold=0.55, source="measured"
        )
        assert r.passed and r.top_score == 0.55

    def test_fails_below(self) -> None:
        r = retrieval_gate(
            score_breakdown={"a": _bd(0.54)}, chunks=1, threshold=0.55, source="measured"
        )
        assert not r.passed

    def test_no_chunks_always_fails(self) -> None:
        assert not retrieval_gate(
            score_breakdown={}, chunks=0, threshold=None, source="none"
        ).passed

    def test_keyword_only_or_no_threshold_needs_one_chunk(self) -> None:
        assert retrieval_gate(
            score_breakdown={"a": _bd(None)}, chunks=1, threshold=0.6, source="measured"
        ).passed
        assert retrieval_gate(
            score_breakdown={"a": _bd(0.1)}, chunks=1, threshold=None, source="none"
        ).passed


def test_shipped_defaults_file_is_valid_json() -> None:
    raw = json.loads(gates.DEFAULTS_PATH.read_text("utf-8"))
    assert raw["version"] == 1 and isinstance(raw["models"], dict)
    for entry in raw["models"].values():
        assert 0.0 < entry["threshold"] < 1.0
        assert {"answerable_kept", "unanswerable_blocked", "n", "measured_on"} <= entry.keys()


def _gate_module():  # benchmarks/ is not a package
    path = Path(__file__).parents[2] / "benchmarks" / "gate.py"
    spec = importlib.util.spec_from_file_location("bench_gate", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_choose_threshold_keeps_97_percent() -> None:
    choose = _gate_module().choose_threshold
    scores = [i / 100 for i in range(40, 100)]  # 60 answerable hits, 0.40 .. 0.99
    t = choose(scores)
    kept = sum(s >= t for s in scores) / len(scores)
    assert kept >= 0.97 and t == 0.41
    assert choose([0.8]) == 0.8


def test_best_dense_ignores_keyword_only_chunks() -> None:
    best = _gate_module().best_dense
    assert best([{"score_breakdown": {"dense_score": 0.4}}, {"score_breakdown": None}]) == 0.4
    assert best([{"score_breakdown": {"dense_score": None}}]) is None


def test_unanswerable_set_is_well_formed() -> None:
    path = Path(__file__).parents[2] / "benchmarks" / "documents" / "unanswerable.jsonl"
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    assert len(rows) == 60 and len({r["q"] for r in rows}) == 60
    assert {r["kind"] for r in rows} == {"absent", "off_topic"}
