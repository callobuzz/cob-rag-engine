"""The adapter between C7's test runs and C1's director."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from callobuzz_rag.api.schemas.evals import ExperimentSettings
from callobuzz_rag.conversation.testing import director_port
from callobuzz_rag.core.config import Settings
from callobuzz_rag.llm.cost import CallCost, CostLedger

SETTINGS = Settings(_env_file=None)
CORPUS = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), applied_embedding=None)
RUNTIME = SimpleNamespace(
    config=SimpleNamespace(
        leads=SimpleNamespace(enabled=True), handoff=SimpleNamespace(enabled=False)
    ),
    version=2,
    draft_revision=None,
    enabled=True,
)


def pair(**kw):
    base = dict(
        query="Is home collection free?",
        history=[{"role": "user", "content": "Hi"}],
        office_hours=None,
        page_url=None,
        language=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def done(route="grounded", threshold=0.4, ui=None):
    return SimpleNamespace(
        label="business_question",
        route=route,
        ui=ui,
        text="It costs 150.",
        sources=[{"file_name": "home.pdf"}, {"file_name": None}],
        usage={"prompt_tokens": 900, "completion_tokens": 40, "model_calls": 2},
        model="answer-model",
        context_texts=["Home collection 150"],
        gate_threshold=threshold,
    )


def director(result, ledger: CostLedger | None = None):
    d = SimpleNamespace(run=AsyncMock(return_value=result), plan=AsyncMock())
    d._deps = SimpleNamespace(llm=SimpleNamespace(cost_ledger=ledger or CostLedger()))
    return d


def test_a_preset_is_expanded_and_explicit_fields_win() -> None:
    snapshot = ExperimentSettings.model_validate({"retrieval": {"preset": "premium", "final_k": 3}})
    merged = director_port.retrieval_overrides(snapshot, SETTINGS)
    assert merged is not None and merged["final_k"] == 3
    assert "top_k" in merged  # from the preset: the preset is not silently dropped
    assert "preset" not in merged


def test_no_retrieval_settings_means_the_corpus_own() -> None:
    assert director_port.retrieval_overrides(ExperimentSettings(), SETTINGS) is None


def test_an_out_of_range_value_is_an_invalid_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = ExperimentSettings.model_validate({"retrieval": {"final_k": 3}})
    monkeypatch.setattr(
        "callobuzz_rag.core.retrieval_presets.validate_retrieval_config",
        lambda cfg: ["final_k: 3 out of range"],
    )
    with pytest.raises(director_port.InvalidSnapshotError, match="final_k"):
        director_port.retrieval_overrides(snapshot, SETTINGS)


def test_the_turn_is_an_unmetered_looking_eval_preview() -> None:
    turn = director_port._turn(CORPUS, RUNTIME, pair(language="hi", office_hours="closed"))
    assert turn.usage_source == "eval" and turn.is_preview and turn.debug is False
    assert turn.persist is False
    assert turn.simulate.language == "hi" and turn.simulate.office_hours == "closed"
    assert [(m.role, m.content) for m in turn.history] == [("user", "Hi")]
    assert turn.runtime is RUNTIME and turn.text == "Is home collection free?"


async def test_answer_pair_reads_the_outcome_and_the_gate_field() -> None:
    outcome, contexts, usage = await director_port.answer_pair(
        director(done()), pair(), CORPUS, RUNTIME
    )
    assert outcome.mode == "full" and outcome.route == "grounded"
    assert outcome.gate_passed is True and outcome.source_files == ("home.pdf",)
    assert outcome.leads_enabled and not outcome.handoff_enabled
    assert contexts == ["Home collection 150"]
    assert (usage.prompt_tokens, usage.llm_calls, usage.model) == (900, 2, "answer-model")


@pytest.mark.parametrize(
    ("route", "threshold", "expected"),
    [
        ("no_answer", 0.4, False),
        ("grounded", 0.4, True),
        ("decline", 0.4, None),
        ("smalltalk", 0.4, None),
        ("grounded", None, None),
    ],
)
async def test_gate_result_from_route_and_threshold(route, threshold, expected) -> None:
    outcome, _, _ = await director_port.answer_pair(
        director(done(route=route, threshold=threshold)), pair(), CORPUS, RUNTIME
    )
    assert outcome.gate_passed is expected


async def test_cost_is_this_turns_share_of_the_shared_ledger() -> None:
    ledger = CostLedger()
    ledger.add(CallCost("answer-model", 1000, 100, 0, 0.5))  # an earlier question
    d = director(done(), ledger)

    async def run(turn):
        ledger.add(CallCost("answer-model", 900, 40, 0, 0.002))
        ledger.add(CallCost("fast-model", 50, 5, 0, None))
        return done()

    d.run = AsyncMock(side_effect=run)
    _, _, usage = await director_port.answer_pair(d, pair(), CORPUS, RUNTIME)
    assert usage.cost_usd == pytest.approx(0.002) and usage.cost_known is False


async def test_a_priced_turn_is_known() -> None:
    ledger = CostLedger()
    d = director(done(), ledger)

    async def run(turn):
        ledger.add(CallCost("answer-model", 900, 40, 0, 0.003))
        return done()

    d.run = AsyncMock(side_effect=run)
    _, _, usage = await director_port.answer_pair(d, pair(), CORPUS, RUNTIME)
    assert usage.cost_usd == pytest.approx(0.003) and usage.cost_known is True


async def test_plan_pair_uses_the_planned_route() -> None:
    d = director(None)
    d.plan = AsyncMock(
        return_value=SimpleNamespace(
            label="buying_intent", route="grounded", gate=SimpleNamespace(passed=True)
        )
    )
    outcome = await director_port.plan_pair(d, pair(), CORPUS, RUNTIME)
    assert (outcome.mode, outcome.label, outcome.gate_passed) == ("fast", "buying_intent", True)
    assert outcome.answer_text is None
