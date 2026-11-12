"""Golden runs: fast (behaviour only) and full (answer + RAGAS + criteria + behaviour)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.conversation.testing.asserts import CaseOutcome
from callobuzz_rag.conversation.testing.director_port import PortUsage
from callobuzz_rag.db.models import EvalRun, EvalRunSample, GoldenPair
from callobuzz_rag.evaluation import golden_runner
from callobuzz_rag.llm.cost import CallCost, CostLedger

CORPUS = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), llm_config=None)
SETTINGS = SimpleNamespace(eval_bootstrap_iterations=100, eval_min_samples=30)


def pair(query: str, *, answer: str | None = None, behaviour: str | None = None) -> GoldenPair:
    return GoldenPair(
        id=uuid.uuid4(),
        corpus_id=CORPUS.id,
        set_id=uuid.uuid4(),
        query=query,
        expected_answer=answer,
        expected_behaviour=behaviour,
        must_include=[],
        history=[],
        origin="human",
        position=0,
        topic="fees",
    )


class FakeSession:
    def __init__(self, run: EvalRun, pairs: list[GoldenPair], claimed: bool = True) -> None:
        self.run, self.pairs, self.added, self.claimed = run, pairs, [], claimed
        self.statements: list[str] = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def get(self, model, key, **kwargs):
        return self.run if model is EvalRun else CORPUS

    async def execute(self, stmt):
        kind = type(stmt).__name__
        self.statements.append(kind)
        result = MagicMock()
        result.scalar_one_or_none.return_value = self.run.id if self.claimed else None
        result.scalars.return_value.all.return_value = self.pairs
        return result

    def add(self, obj) -> None:
        self.added.append(obj)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


CONFIG = SimpleNamespace(leads=SimpleNamespace(enabled=True), handoff=SimpleNamespace(enabled=True))


def runtime(version=3, draft_revision=None):
    return SimpleNamespace(
        config=CONFIG, version=version, draft_revision=draft_revision, enabled=True
    )


def new_run(mode: str) -> EvalRun:
    return EvalRun(
        id=uuid.uuid4(),
        corpus_id=CORPUS.id,
        kind="golden",
        set_id=uuid.uuid4(),
        mode=mode,
        target="published",
        triggered_by="manual",
        status="queued",
        sample_size=0,
        config_snapshot={"assistant": "published"},
        thresholds={},
        criteria=[],
    )


def judge_provider() -> SimpleNamespace:
    return SimpleNamespace(cost_ledger=CostLedger())


def scorer(judge: SimpleNamespace, rows: list[dict], cost: float | None = 0.004):
    """A stand-in for score_samples that records one priced judge call on the ledger."""

    def score(samples, *args, **kwargs):
        judge.cost_ledger.add(CallCost("judge-x", 1000, 100, 0, cost))
        return rows[: len(samples)]

    return score


async def test_fast_run_checks_behaviour_only_and_skips_answer_only_pairs() -> None:
    run = new_run("fast")
    session = FakeSession(run, [pair("Hi", behaviour="smalltalk"), pair("Fees?", answer="150")])
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    outcome = CaseOutcome("fast", "smalltalk", "smalltalk", None, None, None, (), True, True)
    with (
        patch.object(golden_runner, "_config_for", AsyncMock(return_value=runtime())),
        patch.object(golden_runner.director_port, "make_director", return_value=MagicMock()),
        patch.object(golden_runner.director_port, "plan_pair", AsyncMock(return_value=outcome)),
    ):
        await runner.execute(run.id)
    rows = [o for o in session.added if isinstance(o, EvalRunSample)]
    assert len(rows) == 1 and rows[0].passed is True and rows[0].scores == {}
    assert run.status == "completed" and run.totals["questions"] == 1 and run.config_version == 3
    assert run.completed_at is not None and run.sample_size == 1


async def test_a_run_another_worker_holds_is_not_run_twice() -> None:
    run = new_run("fast")
    session = FakeSession(run, [pair("Hi", behaviour="smalltalk")], claimed=False)
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    with patch.object(golden_runner.director_port, "make_director") as make:
        await runner.execute(run.id)
    make.assert_not_called()
    assert session.added == [] and run.status == "queued"


async def test_a_redelivered_run_clears_its_earlier_samples_first() -> None:
    run = new_run("fast")
    session = FakeSession(run, [pair("Hi", behaviour="smalltalk")])
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    outcome = CaseOutcome("fast", "smalltalk", "smalltalk", None, None, None, (), True, True)
    with (
        patch.object(golden_runner, "_config_for", AsyncMock(return_value=runtime())),
        patch.object(golden_runner.director_port, "make_director", return_value=MagicMock()),
        patch.object(golden_runner.director_port, "plan_pair", AsyncMock(return_value=outcome)),
    ):
        await runner.execute(run.id)
    assert session.statements.index("Update") < session.statements.index("Delete")


async def test_a_failing_question_does_not_stop_the_run() -> None:
    run = new_run("fast")
    session = FakeSession(
        run, [pair("Hi", behaviour="smalltalk"), pair("Hello", behaviour="smalltalk")]
    )
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    ok = CaseOutcome("fast", "smalltalk", "smalltalk", None, None, None, (), True, True)
    with (
        patch.object(golden_runner, "_config_for", AsyncMock(return_value=runtime())),
        patch.object(golden_runner.director_port, "make_director", return_value=MagicMock()),
        patch.object(
            golden_runner.director_port,
            "plan_pair",
            AsyncMock(side_effect=[RuntimeError("model down"), ok]),
        ),
    ):
        await runner.execute(run.id)
    rows = [o for o in session.added if isinstance(o, EvalRunSample)]
    assert [r.passed for r in rows] == [False, True] and "model down" in rows[0].error
    assert run.status == "completed" and run.totals["errors"] == 1


async def test_a_run_with_no_assistant_fails_with_a_message() -> None:
    run = new_run("fast")
    session = FakeSession(run, [])
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    with patch.object(golden_runner, "_config_for", AsyncMock(return_value=None)):
        await runner.execute(run.id)
    assert run.status == "failed" and "assistant" in run.error_message


async def test_full_run_scores_judges_and_records_cost() -> None:
    run = new_run("full")
    run.criteria = [
        {"name": "mentions-fee", "definition": "Must mention the 150 fee", "threshold": 0.7}
    ]
    run.thresholds = {"answer_correctness": 0.7, "criterion:mentions-fee": 0.7}
    session = FakeSession(
        run,
        [pair("Is home collection free?", answer="150 within Pune", behaviour="answer")],
    )
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    outcome = CaseOutcome(
        "full",
        "business_question",
        "grounded",
        None,
        True,
        "It is free.",
        ("home.pdf",),
        True,
        True,
    )
    usage = PortUsage(1000, 50, "answer-model", 1, cost_usd=0.002, cost_known=True)
    judge = judge_provider()
    with (
        patch.object(golden_runner, "_config_for", AsyncMock(return_value=runtime(None, 2))),
        patch.object(golden_runner.director_port, "make_director", return_value=MagicMock()),
        patch.object(
            golden_runner.director_port,
            "answer_pair",
            AsyncMock(return_value=(outcome, ["Home collection 150 within Pune"], usage)),
        ),
        patch.object(
            golden_runner,
            "judge_config_for",
            AsyncMock(return_value=SimpleNamespace(model="judge-x", payer="operator")),
        ),
        patch.object(golden_runner, "_judge_provider", return_value=judge),
        patch.object(
            golden_runner,
            "score_samples",
            side_effect=scorer(judge, [{"answer_correctness": 0.2, "faithfulness": 0.9}]),
        ),
        patch.object(
            golden_runner,
            "judge_criteria",
            AsyncMock(return_value={"mentions-fee": (0.0, "Says free; fee is 150.")}),
        ),
        patch.object(
            golden_runner,
            "explain_failure",
            AsyncMock(return_value="Says free, but the documents say 150."),
        ),
        patch.object(golden_runner, "record_usage", AsyncMock()) as meter,
    ):
        await runner.execute(run.id)
    (row,) = [o for o in session.added if isinstance(o, EvalRunSample)]
    assert row.passed is False
    assert row.scores["answer_correctness"] == 0.2 and row.scores["criterion:mentions-fee"] == 0.0
    assert row.reasons["criterion:mentions-fee"] == "Says free; fee is 150."
    assert row.reasons["summary"] == "Says free, but the documents say 150."
    assert row.tags == {"origin": "human", "difficulty": None, "topic": "fees"}
    # answer 0.002 (the turn's own ledger delta) + judge 0.004
    assert run.actual_cost_usd == pytest.approx(0.006) and run.actual_cost_known is True
    assert row.cost_usd == pytest.approx(0.006)
    # the director meters the answers; the runner meters only its own judge calls
    assert meter.await_count == 1
    kwargs = meter.await_args.kwargs
    assert kwargs["source"] == "eval" and kwargs["requests"] == 0 and kwargs["model"] == "judge-x"
    assert kwargs["request_id"] == uuid.uuid5(run.id, "judge")  # BIL3: stable under a re-run
    assert run.metrics["answer_correctness"]["score"] == 0.2
    assert run.metrics["answer_correctness"]["passed"] is False


async def test_an_unpriced_call_makes_the_run_cost_a_floor() -> None:
    run = new_run("full")
    session = FakeSession(run, [pair("Q?", answer="A", behaviour="answer")])
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    outcome = CaseOutcome("full", "business_question", "grounded", None, True, "A", (), True, True)
    usage = PortUsage(10, 5, "mystery", 1, cost_usd=0.0, cost_known=False)
    with (
        patch.object(golden_runner, "_config_for", AsyncMock(return_value=runtime())),
        patch.object(golden_runner.director_port, "make_director", return_value=MagicMock()),
        patch.object(
            golden_runner.director_port,
            "answer_pair",
            AsyncMock(return_value=(outcome, ["ctx"], usage)),
        ),
        patch.object(
            golden_runner,
            "judge_config_for",
            AsyncMock(return_value=SimpleNamespace(model="judge-x", payer="operator")),
        ),
        patch.object(golden_runner, "_judge_provider", return_value=judge_provider()),
        patch.object(golden_runner, "score_samples", return_value=[{"faithfulness": 1.0}]),
        patch.object(golden_runner, "record_usage", AsyncMock()),
    ):
        await runner.execute(run.id)
    assert run.actual_cost_known is False


async def test_only_answers_with_documents_are_scored_by_the_judge_metrics() -> None:
    run = new_run("full")
    session = FakeSession(
        run, [pair("Hi", behaviour="smalltalk"), pair("Fees?", answer="150", behaviour="answer")]
    )
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    small = CaseOutcome("full", "smalltalk", "smalltalk", None, None, "Hello!", (), True, True)
    grounded = CaseOutcome(
        "full", "business_question", "grounded", None, True, "150", (), True, True
    )
    usage = PortUsage(10, 5, "m", 1, cost_usd=0.0, cost_known=True)
    scored: list[int] = []

    def fake_score(samples, *a, **k):
        scored.append(len(samples))
        return [{"faithfulness": 1.0} for _ in samples]

    with (
        patch.object(golden_runner, "_config_for", AsyncMock(return_value=runtime())),
        patch.object(golden_runner.director_port, "make_director", return_value=MagicMock()),
        patch.object(
            golden_runner.director_port,
            "answer_pair",
            AsyncMock(side_effect=[(small, [], usage), (grounded, ["ctx"], usage)]),
        ),
        patch.object(
            golden_runner,
            "judge_config_for",
            AsyncMock(return_value=SimpleNamespace(model="judge-x", payer="operator")),
        ),
        patch.object(golden_runner, "_judge_provider", return_value=judge_provider()),
        patch.object(golden_runner, "score_samples", side_effect=fake_score),
        patch.object(golden_runner, "record_usage", AsyncMock()),
    ):
        await runner.execute(run.id)
    assert scored == [1]
    rows = [o for o in session.added if isinstance(o, EvalRunSample)]
    assert rows[0].scores == {} and rows[1].scores == {"faithfulness": 1.0}


async def test_publish_enqueues_fast_runs_for_sets_with_behaviour() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [uuid.uuid4(), uuid.uuid4()]
    session.execute = AsyncMock(return_value=result)
    with patch.object(golden_runner, "_dispatch") as dispatch:
        assert await golden_runner.enqueue_publish_runs(session, CORPUS.id, 7) == 2
    runs = [c.args[0] for c in session.add.call_args_list]
    assert {(r.kind, r.mode, r.triggered_by, r.config_version) for r in runs} == {
        ("golden", "fast", "publish", 7)
    }
    assert dispatch.call_count == 2


async def test_start_golden_run_refuses_a_busy_set() -> None:
    from callobuzz_rag.api.errors import APIError

    session = AsyncMock()
    busy = MagicMock()
    busy.scalar_one_or_none.return_value = uuid.uuid4()
    session.execute = AsyncMock(return_value=busy)
    with pytest.raises(APIError) as exc:
        await golden_runner.start_golden_run(
            session, CORPUS.id, uuid.uuid4(), mode="fast", triggered_by="setup"
        )
    assert exc.value.code == "eval_run_in_progress"


async def test_start_golden_run_queues_a_run() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    free = MagicMock()
    free.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=free)
    with patch.object(golden_runner, "_dispatch") as dispatch:
        run = await golden_runner.start_golden_run(
            session, CORPUS.id, uuid.uuid4(), mode="fast", triggered_by="setup", assistant=4
        )
    assert run.status == "queued" and run.config_snapshot == {"assistant": 4}
    dispatch.assert_called_once()


async def _run_full(pairs, outcomes, scorer_fn):
    run = new_run("full")
    session = FakeSession(run, pairs)
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    usage = PortUsage(10, 5, "m", 1, cost_usd=0.0, cost_known=True)
    with (
        patch.object(golden_runner, "_config_for", AsyncMock(return_value=runtime())),
        patch.object(golden_runner.director_port, "make_director", return_value=MagicMock()),
        patch.object(
            golden_runner.director_port,
            "answer_pair",
            AsyncMock(side_effect=[(o, c, usage) for o, c in outcomes]),
        ),
        patch.object(
            golden_runner,
            "judge_config_for",
            AsyncMock(return_value=SimpleNamespace(model="judge-x", payer="operator")),
        ),
        patch.object(golden_runner, "_judge_provider", return_value=judge_provider()),
        patch.object(golden_runner, "score_samples", side_effect=scorer_fn),
        patch.object(golden_runner, "judge_criteria", AsyncMock(return_value={})),
        patch.object(golden_runner, "explain_failure", AsyncMock(return_value="wrong")),
        patch.object(golden_runner, "record_usage", AsyncMock()),
    ):
        await runner.execute(run.id)
    return run, [o for o in session.added if isinstance(o, EvalRunSample)]


def _no_answer(text: str = "Sorry, I could not find that.") -> CaseOutcome:
    return CaseOutcome("full", "business_question", "no_answer", None, False, text, (), True, True)


async def test_a_no_answer_reply_to_a_question_with_a_reference_does_not_pass() -> None:
    calls: list[bool] = []

    def fake_score(samples, *a, answer_only=False, **k):
        calls.append(answer_only)
        return [{"answer_correctness": 0.0} for _ in samples]

    _, rows = await _run_full([pair("Fees?", answer="150")], [(_no_answer(), [])], fake_score)
    assert calls == [True]  # scored on answer correctness alone
    assert rows[0].passed is False


async def test_an_unscored_reference_question_is_a_failure_with_a_reason() -> None:
    def fake_score(samples, *a, **k):
        return [{} for _ in samples]

    _, rows = await _run_full([pair("Fees?", answer="150")], [(_no_answer(), [])], fake_score)
    assert rows[0].passed is False
    assert rows[0].reasons["summary"] == "No answer from the documents"


async def test_progress_refreshes_the_heartbeat_so_a_long_run_is_not_stale() -> None:
    grounded = CaseOutcome(
        "full", "business_question", "grounded", None, True, "150", (), True, True
    )

    def fake_score(samples, *a, **k):
        return [{"faithfulness": 1.0} for _ in samples]

    run = new_run("full")
    run.started_at = datetime(2020, 1, 1, tzinfo=UTC)
    session = FakeSession(run, [pair("Fees?", answer="150")])
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    usage = PortUsage(10, 5, "m", 1, cost_usd=0.0, cost_known=True)
    with (
        patch.object(golden_runner, "_claim", AsyncMock(return_value=True)),
        patch.object(golden_runner, "_config_for", AsyncMock(return_value=runtime())),
        patch.object(golden_runner.director_port, "make_director", return_value=MagicMock()),
        patch.object(
            golden_runner.director_port,
            "answer_pair",
            AsyncMock(return_value=(grounded, ["c"], usage)),
        ),
        patch.object(
            golden_runner, "judge_config_for", AsyncMock(return_value=SimpleNamespace(model="j"))
        ),
        patch.object(golden_runner, "_judge_provider", return_value=judge_provider()),
        patch.object(golden_runner, "score_samples", side_effect=fake_score),
        patch.object(golden_runner, "record_usage", AsyncMock()),
    ):
        await runner.execute(run.id)
    assert run.started_at > golden_runner.stale_cutoff()


async def test_a_judge_host_never_receives_the_knowledge_base_key() -> None:
    row = SimpleNamespace(
        judge_model="j",
        judge_provider="openai",
        judge_api_key_encrypted=None,
        judge_base_url="https://evil.example",
    )
    session = MagicMock()
    session.get = AsyncMock(return_value=row)
    resolver = MagicMock()
    with (
        patch("callobuzz_rag.core.config_resolver.ConfigResolver", return_value=resolver),
        patch("callobuzz_rag.core.secrets.encryptor_from_settings", return_value=None),
    ):
        await golden_runner.judge_config_for(
            session, SimpleNamespace(id=uuid.uuid4(), llm_config=None), SETTINGS
        )
    sent = resolver.resolve_llm.call_args_list[-1].kwargs["request_headers"]
    assert "x-llm-base-url" not in sent


async def test_a_run_the_admin_started_meters_waived_and_the_marker_is_not_a_setting() -> None:
    from callobuzz_rag.billing.context import ADMIN_ANSWER

    seen: list[bool] = []

    def make(*args, **kwargs):
        seen.append(ADMIN_ANSWER.get())
        return MagicMock()

    outcome = CaseOutcome("fast", "smalltalk", "smalltalk", None, None, None, (), True, True)
    for admin in (True, False):
        run = new_run("fast")
        if admin:
            run.config_snapshot = {"assistant": "published", golden_runner.ADMIN_INITIATED: True}
        session = FakeSession(run, [pair("Hi", behaviour="smalltalk")])
        runner = golden_runner.GoldenRunner(lambda s=session: s, SETTINGS)
        ADMIN_ANSWER.set(not admin)  # a leftover from earlier work in this context
        with (
            patch.object(golden_runner, "_config_for", AsyncMock(return_value=runtime())),
            patch.object(golden_runner.director_port, "make_director", side_effect=make),
            patch.object(golden_runner.director_port, "plan_pair", AsyncMock(return_value=outcome)),
        ):
            await runner.execute(run.id)
        assert run.status == "completed"  # the marker never fails the settings validation
    assert seen == [True, False]


async def test_runs_queued_by_the_admin_carry_the_marker() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    free = MagicMock()
    free.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=free)
    with patch.object(golden_runner, "_dispatch"):
        run = await golden_runner.start_golden_run(
            session,
            CORPUS.id,
            uuid.uuid4(),
            mode="fast",
            triggered_by="setup",
            assistant=4,
            admin_initiated=True,
        )
    assert run.config_snapshot == {"assistant": 4, "admin_initiated": True}
    assert golden_runner.public_snapshot(run.config_snapshot) == {"assistant": 4}
    result = MagicMock()
    result.scalars.return_value.all.return_value = [uuid.uuid4()]
    session.execute = AsyncMock(return_value=result)
    session.add.reset_mock()
    with patch.object(golden_runner, "_dispatch"):
        await golden_runner.enqueue_publish_runs(session, CORPUS.id, 7, admin_initiated=True)
    assert session.add.call_args.args[0].config_snapshot == {
        "assistant": 7,
        "admin_initiated": True,
    }


async def test_full_run_without_the_add_on_still_judges_criteria_and_skips_ragas() -> None:
    run = new_run("full")
    run.criteria = [{"name": "polite", "definition": "Must be polite", "threshold": 0.5}]
    run.thresholds = {"criterion:polite": 0.5}
    session = FakeSession(run, [pair("Hi?", answer="Hello", behaviour="answer")])
    runner = golden_runner.GoldenRunner(lambda: session, SETTINGS)
    outcome = CaseOutcome(
        "full", "business_question", "grounded", None, True, "Hello!", ("a.pdf",), True, True
    )
    usage = PortUsage(10, 5, "answer-model", 1, cost_usd=0.001, cost_known=True)
    judge = judge_provider()
    with (
        patch.object(golden_runner, "ragas_available", return_value=False),
        patch.object(golden_runner, "_config_for", AsyncMock(return_value=runtime(None, 2))),
        patch.object(golden_runner.director_port, "make_director", return_value=MagicMock()),
        patch.object(
            golden_runner.director_port,
            "answer_pair",
            AsyncMock(return_value=(outcome, ["Hello there"], usage)),
        ),
        patch.object(
            golden_runner,
            "judge_config_for",
            AsyncMock(return_value=SimpleNamespace(model="judge-x", payer="operator")),
        ),
        patch.object(golden_runner, "_judge_provider", return_value=judge),
        patch.object(golden_runner, "score_samples") as ragas,
        patch.object(
            golden_runner, "judge_criteria", AsyncMock(return_value={"polite": (1.0, "Polite.")})
        ),
        patch.object(golden_runner, "record_usage", AsyncMock()),
    ):
        await runner.execute(run.id)
    ragas.assert_not_called()
    (row,) = [o for o in session.added if isinstance(o, EvalRunSample)]
    assert row.scores == {"criterion:polite": 1.0}
    # a reference answer RAGAS never scored is not a failure when RAGAS was not run
    assert row.passed is True and run.status == "completed"
