"""The fair-use gate at every answering route (PIP3 task 4): what each surface does with a
downgrade and with a pause. Through the real app; the business's limits come from the
`fair_use` fixture (tests/unit/conftest.py) and the usage from a patched reader, so each test
states the one fact it is about. The BIL4 balance cases are in `test_billing_hooks_routes.py`."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.responses import Response

from callobuzz_rag.billing.enforcement import NOT_BILLED
from callobuzz_rag.fairuse import gate
from callobuzz_rag.fairuse.limits import FairUseLimits
from callobuzz_rag.fairuse.usage import FairUseUsage
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig
from tests.unit.scope_harness import call, scoped_app
from tests.unit.test_cap_call_sites import assistant_on, cached_answer, quota

FORBIDDEN = ("limit", "fair use", "plan", "billing", "balance", "quota")
FREE = EffectiveProfile(
    id=uuid.uuid4(),
    name="Free",
    label="Economy",
    config=ProfileConfig(answer_route="local", overrides_kb_settings=True, priority="low"),
    source="plan",
    available=True,
)


@contextmanager
def limits(fair_use: Any, used: int, *, per_hour: int = 30) -> Iterator[SimpleNamespace]:
    """The business has `per_hour` questions an hour and has asked `used`; a fallback profile is
    available. Counting, alerts and the Redis client are spied on."""
    fair_use.effective = fair_use.effective.__class__(
        limits=FairUseLimits(questions_per_hour=per_hour),
        policy=fair_use.effective.policy,
        exempt=False,
        overridden=frozenset(),
    )
    spies = SimpleNamespace(
        usage=AsyncMock(return_value=FairUseUsage(questions_per_hour=used)),
        hit=AsyncMock(return_value=True),
        schedule=MagicMock(),
    )
    with (
        patch.object(gate, "read_usage", spies.usage),
        patch.object(gate.counters, "hit_question", spies.hit),
        patch.object(gate.alerts, "schedule", spies.schedule),
        patch.object(gate.tier_profiles, "load_profile", AsyncMock(return_value=FREE)),
        patch.object(gate, "get_redis", MagicMock()),
        patch.object(gate.rails, "engine_breach", AsyncMock(return_value=None)),
    ):
        yield spies


def _assert_fair_use_429(res: Any) -> None:
    assert res.status_code == 429, res.text
    body = res.json()
    assert body["code"] == "fair_use_limit" and body["metric"] == "questions_per_hour"
    assert body["limit"] == 30 and body["used"] == 46
    assert body["resets_at"].endswith(":00Z") and int(res.headers["Retry-After"]) >= 1


# ----------------------------------------------------------------- the legacy API paths


async def test_legacy_api_path_gets_429_fair_use_limit(fair_use) -> None:
    with scoped_app() as (app, _), quota(None), limits(fair_use, 46):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    _assert_fair_use_429(res)


async def test_legacy_stream_gets_the_same_429_before_the_stream_starts(fair_use) -> None:
    with scoped_app() as (app, _), quota(None), limits(fair_use, 46):
        res = await call(app, "ck_a", "POST", "/api/v1/query/stream", json={"query": "hi"})
    _assert_fair_use_429(res)


async def test_conversation_message_legacy_path_gets_the_429(fair_use) -> None:
    with scoped_app() as (app, world), quota(None), limits(fair_use, 46):
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/conversations/{uuid.uuid4()}/messages",
            json={"content": "hi"},
        )
    _assert_fair_use_429(res)


async def test_a_cached_answer_is_refused_to_a_paused_business_too(fair_use) -> None:
    with scoped_app() as (app, _), quota(None), cached_answer() as cache, limits(fair_use, 46):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    _assert_fair_use_429(res)
    assert cache.get.await_count <= 1


async def test_a_cached_answer_below_the_limit_is_served_and_not_counted(fair_use) -> None:
    with scoped_app() as (app, _), quota(None), cached_answer(), limits(fair_use, 5) as spies:
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 200, res.text
    spies.hit.assert_not_awaited()  # it cost nothing, so the minute and hour windows skip it


async def test_a_soft_breach_answers_on_the_fallback_profile(fair_use) -> None:
    from callobuzz_rag.api.routes import query as query_routes

    seen: list[Any] = []
    real = query_routes._resolve_for

    async def spy(request: Any, session: Any, resolver: Any, corpus: Any, **kw: Any) -> Any:
        seen.append(kw.get("profile"))
        return await real(request, session, resolver, corpus, **kw)

    orch = MagicMock()
    orch.llm = MagicMock()
    orch.llm.cost_ledger = None
    orch.query = AsyncMock(
        return_value={
            "answer": "Yes",
            "citations": [],
            "confidence": 0.9,
            "query_id": uuid.uuid4(),
            "model_used": "m",
            "latency_ms": 5,
            "token_usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    )
    with (
        scoped_app() as (app, _),
        quota(None),
        limits(fair_use, 31) as spies,
        patch.object(query_routes, "_resolve_for", spy),
        patch.object(query_routes, "_build_orchestrator", return_value=orch),
        patch.object(query_routes, "record_usage", AsyncMock()),
        patch.object(query_routes, "route_answer_llm", return_value=(orch.llm, None)),
    ):
        res = await call(
            app,
            "ck_a",
            "POST",
            "/api/v1/query",
            json={"query": "Dental cleaning ka price kya hai?"},
        )
    assert res.status_code == 200, res.text
    assert seen[0] is None and seen[1].name == "Free"  # resolved again under the fallback
    spies.hit.assert_awaited_once()
    spies.schedule.assert_called_once()
    assert not any(w in res.text.lower() for w in FORBIDDEN)


async def test_retrieve_is_never_gated(fair_use) -> None:
    with scoped_app() as (app, _), quota(None), limits(fair_use, 999) as spies:
        res = await call(app, "ck_a", "POST", "/api/v1/query/retrieve", json={"query": "hi"})
    assert res.status_code != 429
    spies.usage.assert_not_awaited()


@pytest.mark.parametrize("key", ["admin", "sa"])
async def test_admin_principal_skips_everything_and_is_not_counted(fair_use, key: str) -> None:
    with scoped_app() as (app, world), quota(None), cached_answer(), limits(fair_use, 999) as spies:
        res = await call(
            app,
            key,
            "POST",
            "/api/v1/query",
            json={"query": "hi", "corpus_id": str(world.corpus_a.id)},
        )
    assert res.status_code == 200, res.text
    spies.usage.assert_not_awaited()
    spies.hit.assert_not_awaited()


async def test_cap_still_429s_with_the_same_body(fair_use) -> None:
    with scoped_app() as (app, _), quota((2, 2)), limits(fair_use, 0):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 429
    body = res.json()
    assert body["code"] == "question_cap_reached" and body["limit"] == 2 and body["used"] == 2


async def test_fair_use_off_is_cap_and_billing_only(fair_use) -> None:
    with (
        scoped_app() as (app, _),
        quota(None),
        cached_answer(),
        limits(fair_use, 999) as spies,
    ):
        app.state.settings.fair_use_enabled = False
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 200, res.text
    spies.usage.assert_not_awaited()


# ----------------------------------------------------------------- the director paths


@pytest.mark.assistant_on
async def test_director_hard_breach_runs_paused_by_fair_use(fair_use) -> None:
    from callobuzz_rag.api.schemas.query import QueryResponse

    done = QueryResponse(
        answer="pause message",
        confidence=1.0,
        query_id=uuid.uuid4(),
        model_used="m",
        latency_ms=1,
        paused=True,
        pause_reason="fair_use",
    )
    with scoped_app() as (app, _), quota(None), limits(fair_use, 46), assistant_on() as (answer, _):
        answer.return_value = done
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 200, res.text
    kwargs = answer.await_args.kwargs
    assert kwargs["paused_by"] == "fair_use" and kwargs["billing_paused"] is False
    body = res.json()
    assert body["paused"] is True and body["pause_reason"] == "fair_use"  # API callers are told


@pytest.mark.assistant_on
async def test_director_soft_breach_answers_with_the_fallback_profile(fair_use) -> None:
    from callobuzz_rag.api.schemas.query import QueryResponse

    done = QueryResponse(
        answer="ok", confidence=1.0, query_id=uuid.uuid4(), model_used="m", latency_ms=1
    )
    with scoped_app() as (app, _), quota(None), limits(fair_use, 31), assistant_on() as (answer, _):
        answer.return_value = done
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 200 and res.json()["paused"] is False
    kwargs = answer.await_args.kwargs
    assert kwargs["paused_by"] is None and kwargs["profile"].name == "Free"
    assert kwargs["profile"].config.priority == "low"


@pytest.mark.assistant_on
async def test_director_stream_hard_breach_is_paused_by_fair_use(fair_use) -> None:
    with scoped_app() as (app, _), quota(None), limits(fair_use, 46), assistant_on() as (_, stream):
        stream.return_value = MagicMock()
        await call(app, "ck_a", "POST", "/api/v1/query/stream", json={"query": "hi"})
    assert stream.await_args.kwargs["paused_by"] == "fair_use"


@pytest.mark.assistant_on
async def test_conversation_message_director_gets_the_pause_and_the_profile(fair_use) -> None:
    from callobuzz_rag.conversation import surfaces

    runtime = SimpleNamespace(config=SimpleNamespace(enabled=True))
    reply = AsyncMock(return_value=Response(status_code=201))
    with (
        scoped_app() as (app, world),
        quota(None),
        limits(fair_use, 31),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch.object(surfaces, "conversation_reply", reply),
    ):
        await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/conversations/{uuid.uuid4()}/messages",
            json={"content": "hi"},
        )
    assert reply.await_args.kwargs["profile"].name == "Free"
    assert reply.await_args.kwargs["paused_by"] is None


# --------------------------------------------------------------------------------- widget


async def _widget_chat(fair_use: Any, used: int, *, assistant: bool) -> tuple[Any, AsyncMock]:
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.conversation import surfaces
    from callobuzz_rag.widget.security import LimitResult
    from tests.unit.test_widget import SITE, _client, _key, _session, _settings, _use_session

    app = create_app()
    app.state.settings = _settings()
    app.state.widget_rate_limiter = SimpleNamespace(check=AsyncMock(return_value=LimitResult(True)))
    corpus = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        qdrant_collection="c",
        retrieval_config=None,
        llm_config=None,
        prompt_config=None,
    )
    _use_session(app, _session(_key(corpus.id), corpus))
    reply = AsyncMock(return_value=MagicMock())
    runtime = SimpleNamespace(config=SimpleNamespace(enabled=True)) if assistant else None
    with (
        quota(None),
        limits(fair_use, used),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch.object(surfaces, "widget_reply", reply),
        patch("callobuzz_rag.api.routes.widget._build_orchestrator", MagicMock()),
    ):
        async with await _client(app) as c:
            res = await c.post(
                "/api/v1/widget/chat",
                json={"message": "What are your timings?"},
                headers={"X-Widget-Key": "pk_x", "Origin": SITE},
            )
    return res, reply


async def test_widget_without_the_assistant_is_a_polite_409_never_a_word_about_limits(
    fair_use,
) -> None:
    res, reply = await _widget_chat(fair_use, 46, assistant=False)
    assert res.status_code == 409, res.text
    assert res.json() == {
        "detail": "This assistant is not available right now.",
        "code": "assistant_disabled",
    }
    assert not any(w in res.text.lower() for w in FORBIDDEN)
    reply.assert_not_awaited()


@pytest.mark.assistant_on
async def test_widget_hard_breach_reaches_the_director_paused(fair_use) -> None:
    _, reply = await _widget_chat(fair_use, 46, assistant=True)
    assert reply.await_args.kwargs["paused_by"] == "fair_use"


@pytest.mark.assistant_on
async def test_widget_soft_breach_answers_on_the_fallback(fair_use) -> None:
    _, reply = await _widget_chat(fair_use, 31, assistant=True)
    assert reply.await_args.kwargs["paused_by"] is None
    assert reply.await_args.kwargs["profile"].name == "Free"


# --------------------------------------------------------------------------- golden runs


async def test_a_golden_run_is_never_downgraded_but_a_pause_stops_it_before_it_starts(
    fair_use,
) -> None:
    from callobuzz_rag.api.routes import evals

    request = SimpleNamespace(
        state=SimpleNamespace(is_admin=False),
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_factory=None, settings=SimpleNamespace(fair_use_enabled=True)
            )
        ),
    )
    corpus = SimpleNamespace(tenant_id=uuid.uuid4(), llm_config=None)
    with (
        quota(None),
        limits(fair_use, 31),
        patch(
            "callobuzz_rag.billing.enforcement.billing_state", AsyncMock(return_value=NOT_BILLED)
        ),
    ):
        await evals._check_question_room(AsyncMock(), request, corpus, 3)  # soft: still runs
    with (
        quota(None),
        limits(fair_use, 46),
        patch(
            "callobuzz_rag.billing.enforcement.billing_state", AsyncMock(return_value=NOT_BILLED)
        ),
        pytest.raises(Exception) as err,
    ):
        await evals._check_question_room(AsyncMock(), request, corpus, 3)
    assert getattr(err.value, "status_code", None) == 429
    assert getattr(err.value, "code", None) == "fair_use_limit"


async def test_a_golden_run_is_refused_when_the_balance_is_out_though_the_decision_downgrades(
    fair_use,
) -> None:
    """FU-1: the default policy downgrades a business with no balance to the free model, which
    a golden run never does (D10), so the run was neither refused nor run on the fallback."""
    from callobuzz_rag.api.routes import evals
    from callobuzz_rag.billing.enforcement import BillingState

    paused = BillingState(True, True, "prepaid_empty", -120, "INR")
    request = SimpleNamespace(
        state=SimpleNamespace(is_admin=False),
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_factory=None, settings=SimpleNamespace(fair_use_enabled=True)
            )
        ),
    )
    corpus = SimpleNamespace(tenant_id=uuid.uuid4(), llm_config=None)
    with (
        quota(None),
        limits(fair_use, 1),
        patch("callobuzz_rag.billing.enforcement.billing_state", AsyncMock(return_value=paused)),
        patch("callobuzz_rag.accounts.caps._support_contact", AsyncMock(return_value="support")),
        pytest.raises(Exception) as err,
    ):
        await evals._check_question_room(AsyncMock(), request, corpus, 3)
    assert getattr(err.value, "status_code", None) == 402
    assert getattr(err.value, "code", None) == "billing_paused"


async def test_a_golden_run_is_refused_at_a_money_ceiling_not_run_on_a_downgrade() -> None:
    """FU-1: an AI-spend ceiling or the engine rail downgrades the answer; a golden run would
    still spend on the paid model, so it is refused with the fair-use 429."""
    from callobuzz_rag.fairuse.decision import decide
    from callobuzz_rag.fairuse.limits import Breach, FairUsePolicy

    decision = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=[Breach("ai_spend_per_day", "hard", 5_000_000, 6_000_000, None)],
        policy=FairUsePolicy(),
        plan_profile=FREE,
        downgrade_to=FREE,
        exempt=False,
    )
    assert decision.action == "downgrade"
    with pytest.raises(Exception) as err:
        await gate.refuse_measured(decision, SimpleNamespace())
    assert getattr(err.value, "status_code", None) == 429
    assert getattr(err.value, "code", None) == "fair_use_limit"
