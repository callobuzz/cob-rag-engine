"""PIP1: the legacy answering routes (assistant off) behind the answer lanes.

`/query/stream` shows one `queued` stage, then answers; `/query` answers 503 `answers_busy` with
`Retry-After: 30`; the website widget says only its usual "not available" sentence. A slot is
released on success, on an exception and when the client goes away. A profile that answers with
the local model draws from the `local` pool."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.routes.query import slotted_stream
from callobuzz_rag.core.config import Settings
from callobuzz_rag.tiers.lanes import AnswerLanes, LaneConfig, slot_key
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig
from callobuzz_rag.tiers.slot import answer_slot, slot_spec
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.test_query_stream import (  # noqa: F401
    ADMIN,
    _events,
    _orchestrator,
    corpus,
    log_session,
)
from tests.unit.test_widget import SITE, _client, _key, _session, _use_session
from tests.unit.test_widget import _orchestrator as widget_orchestrator

pytestmark = pytest.mark.usefixtures("unlimited_plan")

CFG = LaneConfig(slots=1, reserve_high=0, low_max=0, lease_s=60, max_wait_s=0.25, poll_ms=(5, 10))
BUSY_WORDS = ("busy", "queue", "slot", "limit", "plan", "billing", "fair")


def make_profile(**config: Any) -> EffectiveProfile:
    return EffectiveProfile(
        id=uuid.uuid4(),
        name="T",
        label="T",
        config=ProfileConfig.model_validate(config),
        source="plan",
        available=True,
    )


class Lanes:
    def __init__(self) -> None:
        self.redis = FakeRedis()
        self.lanes = AnswerLanes(
            self.redis,
            {"engine": CFG, "local": LaneConfig(1, 0, 0, 60, 0.25, (5, 10))},
        )

    async def hold(self, pool: str = "engine") -> Any:
        return await self.lanes.acquire(
            pool=pool,  # type: ignore[arg-type]
            tenant_id=uuid.uuid4(),
            priority="normal",
            max_concurrency=None,
        )

    async def free(self, pool: str = "engine") -> bool:
        return await self.redis.get(slot_key(pool, 0)) is None


@pytest.fixture
def lanes() -> Lanes:
    return Lanes()


def _app(lanes: Lanes, corpus: Any, log_session: Any) -> Any:  # noqa: F811
    application = create_app()
    application.state.settings = Settings(
        _env_file=None,
        admin_api_key="test-admin-key",
        llm_api_key="test",
        answer_slots=1,
    )
    application.state.answer_lanes = lanes.lanes

    @asynccontextmanager
    async def factory():  # type: ignore[no-untyped-def]
        yield log_session

    application.state.session_factory = factory
    from callobuzz_rag.api.deps import get_session

    request_session = AsyncMock()
    request_session.get = AsyncMock(return_value=corpus)

    async def override():  # type: ignore[no-untyped-def]
        yield request_session

    application.dependency_overrides[get_session] = override
    return application


@pytest.fixture
def stream_app(lanes: Lanes, corpus: Any, log_session: Any) -> Any:  # noqa: F811
    return _app(lanes, corpus, log_session)


async def _stream(app: Any, corpus: Any, orch: Any) -> Any:  # noqa: F811
    async def fake_record(_factory: Any, **_kwargs: Any) -> None:
        return None

    with (
        patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
        patch("callobuzz_rag.api.routes.query.record_usage_new_session", fake_record),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.post(
                "/api/v1/query/stream",
                headers=ADMIN,
                json={"query": "Can I return this?", "corpus_id": str(corpus.id)},
            )


# ---------------------------------------------------------------- /query/stream


async def test_a_queued_stream_says_so_once_then_answers(
    stream_app: Any,
    lanes: Lanes,
    corpus: Any,  # noqa: F811
) -> None:
    blocker = await lanes.hold()
    orch, _c, _d = _orchestrator(["Yes, ", "within 30 days."])

    async def free_soon() -> None:
        await asyncio.sleep(0.08)
        await blocker.release()

    freeing = asyncio.create_task(free_soon())
    res = await _stream(stream_app, corpus, orch)
    await freeing
    events = _events(res.text)
    stages = [e[1]["stage"] for e in events if e[0] == "stage"]
    assert stages.count("queued") == 1 and stages[0] == "queued"
    assert stages[1:] == ["understand", "search", "write"]
    assert events[-1][0] == "done"
    assert await lanes.free()  # released after the answer


async def test_no_queued_stage_when_a_slot_is_free(
    stream_app: Any,
    lanes: Lanes,
    corpus: Any,  # noqa: F811
) -> None:
    orch, _c, _d = _orchestrator(["Hi"])
    res = await _stream(stream_app, corpus, orch)
    assert "queued" not in [e[1]["stage"] for e in _events(res.text) if e[0] == "stage"]
    assert await lanes.free()


async def test_a_stream_that_waits_too_long_is_an_error_event_and_never_searches(
    stream_app: Any,
    lanes: Lanes,
    corpus: Any,  # noqa: F811
) -> None:
    blocker = await lanes.hold()
    orch, _c, _d = _orchestrator(["Hi"])
    res = await _stream(stream_app, corpus, orch)
    events = _events(res.text)
    assert events[-1][0] == "error" and events[-1][1]["code"] == "answers_busy"
    orch.run_retrieval_pipeline.assert_not_awaited()
    await blocker.release()


async def test_the_slot_is_released_when_retrieval_fails(
    stream_app: Any,
    lanes: Lanes,
    corpus: Any,  # noqa: F811
) -> None:
    orch, _c, _d = _orchestrator(["x"], retrieval_error=True)
    res = await _stream(stream_app, corpus, orch)
    assert _events(res.text)[-1][0] == "error"
    assert await lanes.free()


async def test_the_slot_is_released_when_generation_fails(
    stream_app: Any,
    lanes: Lanes,
    corpus: Any,  # noqa: F811
) -> None:
    orch, _c, _d = _orchestrator(["part"], fail=True)
    await _stream(stream_app, corpus, orch)
    assert await lanes.free()


async def test_the_slot_is_released_when_the_client_leaves_mid_stream(
    lanes: Lanes,
) -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=Settings(_env_file=None, answer_slots=1), answer_lanes=lanes.lanes
            )
        )
    )

    async def inner() -> Any:
        for n in range(10):
            yield f"frame {n}"
            await asyncio.sleep(0)

    stream = slotted_stream(request, make_profile(), uuid.uuid4(), inner())  # type: ignore[arg-type]
    assert await stream.__anext__() == "frame 0"
    assert not await lanes.free()  # held while answering
    await stream.aclose()  # the client went away
    assert await lanes.free()


async def test_a_busy_wait_closes_the_unstarted_answer(lanes: Lanes) -> None:
    blocker = await lanes.hold()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=Settings(_env_file=None, answer_slots=1), answer_lanes=lanes.lanes
            )
        )
    )
    started = False

    async def inner() -> Any:
        nonlocal started
        started = True
        yield "never"

    frames = [f async for f in slotted_stream(request, make_profile(), uuid.uuid4(), inner())]  # type: ignore[arg-type]
    assert not started and "answers_busy" in frames[-1]
    await blocker.release()


# ---------------------------------------------------------------- /query (JSON)


def _cache_miss() -> MagicMock:
    cache = MagicMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    return cache


def _query_orchestrator() -> MagicMock:
    orch = MagicMock()
    orch.llm = MagicMock()
    orch.llm.language_models = {}
    orch.query = AsyncMock(
        return_value={
            "answer": "Yes, within 30 days.",
            "citations": [],
            "confidence": 1.0,
            "query_id": uuid.uuid4(),
            "model_used": "m",
            "latency_ms": 5,
            "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )
    return orch


async def _post_query(app: Any, corpus: Any, orch: Any) -> Any:  # noqa: F811
    with (
        patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
        patch("callobuzz_rag.api.routes.query._get_query_cache", return_value=_cache_miss()),
        patch("callobuzz_rag.api.routes.query.record_usage", AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.post(
                "/api/v1/query",
                headers=ADMIN,
                json={"query": "Can I return this?", "corpus_id": str(corpus.id)},
            )


async def test_legacy_query_answers_503_when_busy_with_retry_after(
    stream_app: Any,
    lanes: Lanes,
    corpus: Any,  # noqa: F811
) -> None:
    blocker = await lanes.hold()
    orch = _query_orchestrator()
    res = await _post_query(stream_app, corpus, orch)
    assert res.status_code == 503, res.text
    assert res.json()["code"] == "answers_busy"
    assert res.headers["Retry-After"] == "30"
    orch.query.assert_not_awaited()
    await blocker.release()


async def test_legacy_query_releases_its_slot_on_success_and_on_failure(
    stream_app: Any,
    lanes: Lanes,
    corpus: Any,  # noqa: F811
) -> None:
    res = await _post_query(stream_app, corpus, _query_orchestrator())
    assert res.status_code == 200, res.text
    assert await lanes.free()
    broken = _query_orchestrator()
    broken.query = AsyncMock(side_effect=RuntimeError("provider down"))
    res = await _post_query(stream_app, corpus, broken)
    assert res.status_code == 500
    assert await lanes.free()


# ---------------------------------------------------------------- /query/retrieve


def _retrieve_orchestrator() -> MagicMock:
    orch = MagicMock()
    pipeline = MagicMock()
    pipeline.assembled.cited_chunks = []
    pipeline.also_in = {}
    pipeline.score_breakdown = {}
    pipeline.processed_query = "q"
    pipeline.stage_timings_ms = {}
    orch.run_retrieval_pipeline = AsyncMock(return_value=pipeline)
    return orch


async def _post_retrieve(app: Any, corpus: Any, orch: Any) -> Any:  # noqa: F811
    with patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.post(
                "/api/v1/query/retrieve",
                headers=ADMIN,
                json={"query": "Can I return this?", "corpus_id": str(corpus.id)},
            )


async def test_retrieve_answers_503_when_busy_and_never_searches(
    stream_app: Any,
    lanes: Lanes,
    corpus: Any,  # noqa: F811
) -> None:
    blocker = await lanes.hold()
    orch = _retrieve_orchestrator()
    res = await _post_retrieve(stream_app, corpus, orch)
    assert res.status_code == 503, res.text
    assert res.json()["code"] == "answers_busy"
    orch.run_retrieval_pipeline.assert_not_awaited()
    await blocker.release()


async def test_retrieve_releases_its_slot_on_success_and_on_failure(
    stream_app: Any,
    lanes: Lanes,
    corpus: Any,  # noqa: F811
) -> None:
    res = await _post_retrieve(stream_app, corpus, _retrieve_orchestrator())
    assert res.status_code == 200, res.text
    assert await lanes.free()
    broken = _retrieve_orchestrator()
    broken.run_retrieval_pipeline = AsyncMock(side_effect=RuntimeError("search is down"))
    res = await _post_retrieve(stream_app, corpus, broken)
    assert res.status_code == 500
    assert await lanes.free()


# ---------------------------------------------------------------- the website widget


async def test_widget_busy_is_the_usual_not_available_sentence(
    lanes: Lanes,
    corpus: Any,  # noqa: F811
) -> None:
    application = create_app()
    application.state.settings = Settings(
        _env_file=None,
        admin_api_key="test-admin-key",
        llm_api_key="test",
        public_url="https://rag.example.com",
        answer_slots=1,
    )
    application.state.answer_lanes = lanes.lanes
    from callobuzz_rag.widget.security import LimitResult

    application.state.widget_rate_limiter = SimpleNamespace(
        check=AsyncMock(return_value=LimitResult(True))
    )
    _use_session(application, _session(_key(corpus.id), corpus))
    blocker = await lanes.hold()
    with (
        patch(
            "callobuzz_rag.api.routes.widget._build_orchestrator",
            return_value=widget_orchestrator(["Hi"]),
        ),
        patch("callobuzz_rag.api.routes.widget._get_config_resolver") as resolver,
    ):
        resolver.return_value.resolve_retrieval.return_value = MagicMock()
        resolver.return_value.resolve_llm.return_value = MagicMock(
            language_models={}, model="engine/model"
        )
        async with await _client(application) as c:
            res = await c.post(
                "/api/v1/widget/chat",
                json={"message": "Can I return this?"},
                headers={"X-Widget-Key": "pk_x", "Origin": SITE},
            )
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["code"] == "assistant_disabled"
    assert body["detail"] == "This assistant is not available right now."
    assert not any(word in res.text.lower() for word in BUSY_WORDS)
    await blocker.release()


# ---------------------------------------------------------------- pools and profiles


async def test_a_local_profile_draws_from_the_local_pool(lanes: Lanes) -> None:
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(_env_file=None, answer_slots=1), answer_lanes=lanes.lanes
        )
    )
    local = make_profile(answer_route="local", overrides_kb_settings=True)
    async with answer_slot(SimpleNamespace(app=app), local, uuid.uuid4()) as lease:
        assert lease is not None and lease.pool == "local"
        assert not await lanes.free("local") and await lanes.free("engine")
    assert await lanes.free("local")


async def test_no_lane_means_no_slot_and_no_redis() -> None:
    # The default install: no pool, no per-business limit: nothing to wait for.
    app = SimpleNamespace(state=SimpleNamespace(settings=Settings(_env_file=None)))
    assert slot_spec(app, make_profile()) is None
    async with answer_slot(SimpleNamespace(app=app), make_profile(), uuid.uuid4()) as lease:
        assert lease is None
    assert not hasattr(app.state, "answer_lanes")  # no Redis client was even created


async def test_a_business_limit_alone_creates_a_slot() -> None:
    pool_off = AnswerLanes(FakeRedis(), {"engine": replace(CFG, slots=0)})
    app = SimpleNamespace(
        state=SimpleNamespace(settings=Settings(_env_file=None), answer_lanes=pool_off)
    )
    limited = make_profile(max_concurrency=1)
    tenant = uuid.uuid4()
    async with answer_slot(SimpleNamespace(app=app), limited, tenant) as lease:
        assert lease is not None and lease.tenant_slot == 0 and lease.slot is None


async def test_golden_runs_wait_ten_times_longer(lanes: Lanes) -> None:
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(_env_file=None, answer_slots=1, answer_queue_max_wait_seconds=20),
            answer_lanes=lanes.lanes,
        )
    )
    patient = slot_spec(app, make_profile(), patient=True)
    normal = slot_spec(app, make_profile())
    assert patient is not None and normal is not None
    assert normal.max_wait_s is None and patient.max_wait_s == 200.0


async def test_a_slot_is_released_when_the_block_raises(lanes: Lanes) -> None:
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(_env_file=None, answer_slots=1), answer_lanes=lanes.lanes
        )
    )
    with pytest.raises(RuntimeError):
        async with answer_slot(SimpleNamespace(app=app), make_profile(), uuid.uuid4()):
            raise RuntimeError("the model failed")
    assert await lanes.free()


# ---------------------------------------------------------------- the widget's words


def test_the_widget_has_words_for_the_queued_stage_in_all_three_languages() -> None:
    from pathlib import Path

    core = (
        Path(__file__).resolve().parents[2] / "src/callobuzz_rag/widget/static/widget-core.js"
    ).read_text(encoding="utf-8")
    assert core.count("stage_queued:") == 3
    assert "One moment" in core and "बस एक पल" in core and "Bas ek pal" in core


async def test_retrieve_options_cannot_widen_an_overriding_profile(
    stream_app: Any,
    lanes: Lanes,
    corpus: Any,  # noqa: F811
    tenant_profile: Any,
) -> None:
    """PIP-8: the Free profile's limits hold against `options` on the search-only route."""
    tenant_profile.profile = make_profile(
        overrides_kb_settings=True, top_k=8, final_k=3, hyde_enabled=False
    )
    orch = _retrieve_orchestrator()
    with patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch):
        async with AsyncClient(
            transport=ASGITransport(app=stream_app), base_url="http://test"
        ) as c:
            res = await c.post(
                "/api/v1/query/retrieve",
                headers=ADMIN,
                json={
                    "query": "Can I return this?",
                    "corpus_id": str(corpus.id),
                    "options": {"top_k": 100, "final_k": 30, "enable_hyde": True},
                },
            )
    assert res.status_code == 200, res.text
    kwargs = orch.run_retrieval_pipeline.await_args.kwargs
    assert (kwargs["top_k"], kwargs["final_k"], kwargs["enable_hyde"]) == (8, 3, False)
