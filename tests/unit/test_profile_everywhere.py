"""PIP1: every answering surface and AI helper resolves its configuration through the
business's pipeline profile (`tiers.resolve.resolve_with_profile`)."""

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.conversation.envelope import DoneEvent
from callobuzz_rag.conversation.store import RuntimeConfig
from callobuzz_rag.conversation.types import Route, TurnLabel
from callobuzz_rag.core.config import Settings
from callobuzz_rag.tiers import profiles as tier_profiles
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig
from tests.unit.conversation_helpers import make_config

SRC = Path(__file__).resolve().parents[2] / "src" / "callobuzz_rag"

SURFACES = [
    "api/routes/query.py",
    "api/routes/conversations.py",
    "api/routes/widget.py",
    "api/routes/evals.py",
    "api/routes/assistant.py",
    "conversation/surfaces.py",
    "conversation/setup/service.py",
    "evaluation/golden_runner.py",
    "worker/conversation_tasks.py",
]
# Not business answers, on purpose: COB answers with its own configuration (D11), the RAGAS
# judge is engine-level, the optimizer measures the knowledge base's raw configuration.
EXEMPT = ["cob/orchestrator.py", "evaluation/runner.py", "evaluation/param_eval.py"]


@pytest.mark.parametrize("path", SURFACES)
def test_no_surface_resolves_without_the_profile(path: str) -> None:
    src = (SRC / path).read_text(encoding="utf-8")
    assert ".resolve_llm(" not in src, f"{path} must call tiers.resolve.resolve_with_profile"
    assert ".resolve_retrieval(" not in src, f"{path} must call resolve_with_profile"
    assert "resolve_with_profile" in src or "build_director" in src or "_resolve_llm" in src


def test_the_exempt_paths_are_the_documented_ones() -> None:
    for path in EXEMPT:
        assert (SRC / path).exists(), path


def profile(**config: Any) -> EffectiveProfile:
    return EffectiveProfile(
        id=uuid.uuid4(),
        name="Test",
        label="Test",
        config=ProfileConfig.model_validate(config),
        source="plan",
        available=True,
    )


CORPUS = SimpleNamespace(
    id=uuid.uuid4(),
    tenant_id=uuid.uuid4(),
    qdrant_collection="corpus_x",
    retrieval_config=None,
    llm_config=None,
    prompt_config=None,
    applied_embedding={"model": "BAAI/bge-m3"},
)
SETTINGS = Settings(
    _env_file=None, admin_api_key="test-admin-key", llm_api_key="sk-engine", llm_model="engine/m"
)


def request_for() -> Any:
    from callobuzz_rag.core.config_resolver import ConfigResolver

    state = SimpleNamespace(
        settings=SETTINGS,
        config_resolver=ConfigResolver(SETTINGS),
        session_factory=MagicMock(),
        assistant_caches=(None, None),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), headers={}, state=SimpleNamespace())


async def test_resolve_for_applies_the_plan_profile_and_returns_it() -> None:
    from callobuzz_rag.api.routes.query import _get_config_resolver, _resolve_for

    chosen = profile(
        answer_model="p/model", reranker="off", multi_query_count=0, overrides_kb_settings=True
    )
    request = request_for()
    with patch.object(tier_profiles, "plan_profile", AsyncMock(return_value=chosen)) as lookup:
        llm, retrieval, got = await _resolve_for(
            request, MagicMock(), _get_config_resolver(request), CORPUS, headers=None
        )
    assert got is chosen
    assert (llm.model, retrieval.reranking_enabled, retrieval.multi_query_count) == (
        "p/model",
        False,
        0,
    )
    assert lookup.await_args.args[1] == CORPUS.tenant_id


async def test_resolve_for_uses_the_profile_a_caller_already_decided() -> None:
    from callobuzz_rag.api.routes.query import _get_config_resolver, _resolve_for

    decided = profile(answer_model="downgrade/model", overrides_kb_settings=True)
    request = request_for()
    with patch.object(tier_profiles, "plan_profile", AsyncMock()) as lookup:
        llm, _, got = await _resolve_for(
            request,
            MagicMock(),
            _get_config_resolver(request),
            CORPUS,
            headers=None,
            profile=decided,
        )
    lookup.assert_not_awaited()
    assert got is decided and llm.model == "downgrade/model"


def test_build_director_answers_with_the_profile_and_without_one_changes_nothing() -> None:
    chosen = profile(answer_model="p/model", overrides_kb_settings=True)
    with_profile = surfaces.build_director(
        request_for(), CORPUS, use_headers=False, options=None, profile=chosen
    )
    plain = surfaces.build_director(request_for(), CORPUS, use_headers=False, options=None)
    assert with_profile._deps.model_name == "p/model"
    assert plain._deps.model_name == "engine/m"  # COB and other callers: the no-op profile


def test_build_director_gives_the_profile_retrieval_settings_to_the_turn() -> None:
    chosen = profile(top_k=7, reranker="off", overrides_kb_settings=True)
    director = surfaces.build_director(
        request_for(), CORPUS, use_headers=False, options=None, profile=chosen
    )
    kwargs = director._deps.retrieval_kwargs
    assert kwargs["top_k"] == 7 and kwargs["enable_reranking"] is False


# ---- the director path: follow-up chips off reaches the turn ----

ADMIN = {"X-API-Key": "test-admin-key"}
QID = uuid.uuid4()


class FakeDirector:
    def __init__(self) -> None:
        self.turns: list[Any] = []

    async def run(self, turn: Any) -> DoneEvent:
        self.turns.append(turn)
        return DoneEvent(
            query_id=QID,
            label=TurnLabel.BUSINESS_QUESTION,
            route=Route.GROUNDED,
            language="en",
            cta=None,
            timings={},
            claims_removed=0,
            model="m",
            usage={
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "cost_usd": 0.0,
                "cost_known": True,
                "model_calls": 1,
            },
            latency_ms=1,
            text="ok",
            message_id=None,
            conversation_id=None,
            sources=[],
            assistant_version=1,
        )

    async def aclose(self) -> None:
        return None


@pytest.mark.usefixtures("unlimited_plan")
@pytest.mark.assistant_on
async def test_followups_off_reaches_the_director() -> None:
    from callobuzz_rag.api.deps import get_session

    app = create_app()
    app.state.settings = SETTINGS
    session = AsyncMock()
    session.get = AsyncMock(return_value=CORPUS)

    async def override() -> Any:
        yield session

    app.dependency_overrides[get_session] = override
    director = FakeDirector()
    runtime = RuntimeConfig(config=make_config(), version=1, draft_revision=None, enabled=True)
    assert runtime.config.conversation.ai_followups is True
    cheap = profile(followups=False, priority="low")
    built = MagicMock(return_value=director)
    with (
        patch.object(tier_profiles, "plan_profile", AsyncMock(return_value=cheap)),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch.object(surfaces, "build_director", built),
        patch.object(surfaces, "conversation_context", AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            res = await client.post(
                "/api/v1/query",
                json={"query": "What does a root canal cost?", "corpus_id": str(CORPUS.id)},
                headers=ADMIN,
            )
    assert res.status_code == 200, res.text
    assert built.call_args.kwargs["profile"] is cheap
    assert director.turns[0].runtime.config.conversation.ai_followups is False


@pytest.mark.usefixtures("unlimited_plan")
@pytest.mark.assistant_on
@pytest.mark.parametrize(
    ("config", "settings_kw", "pool"),
    [
        ({}, {}, None),  # the default install: no lane applies, nothing is waited for
        ({"max_concurrency": 2}, {}, "engine"),  # a per-business limit alone
        ({"priority": "low"}, {"answer_slots": 4}, "engine"),  # the engine-wide pool
        (
            {"answer_route": "local", "overrides_kb_settings": True},
            {"answer_slots": 4},
            "local",
        ),
    ],
)
async def test_the_director_turn_carries_the_lane_the_profile_asks_for(
    config: dict[str, Any], settings_kw: dict[str, Any], pool: str | None
) -> None:
    from callobuzz_rag.api.deps import get_session

    app = create_app()
    app.state.settings = Settings(
        _env_file=None, admin_api_key="test-admin-key", llm_api_key="sk-engine", **settings_kw
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=CORPUS)

    async def override() -> Any:
        yield session

    app.dependency_overrides[get_session] = override
    director = FakeDirector()
    runtime = RuntimeConfig(config=make_config(), version=1, draft_revision=None, enabled=True)
    chosen = profile(**config)
    with (
        patch.object(tier_profiles, "plan_profile", AsyncMock(return_value=chosen)),
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch.object(surfaces, "build_director", MagicMock(return_value=director)),
        patch.object(surfaces, "conversation_context", AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            res = await client.post(
                "/api/v1/query",
                json={"query": "What does a root canal cost?", "corpus_id": str(CORPUS.id)},
                headers=ADMIN,
            )
    assert res.status_code == 200, res.text
    slot = director.turns[0].slot
    if pool is None:
        assert slot is None
    else:
        assert slot is not None and slot.pool == pool
        assert slot.priority == chosen.config.priority
        assert slot.max_concurrency == chosen.config.max_concurrency


# ---- the worker's helper calls ----


def test_worker_helper_calls_use_the_profile_fast_model() -> None:
    from callobuzz_rag.worker import conversation_tasks as tasks

    chosen = profile(fast_model="p/fast", overrides_kb_settings=True)
    corpus = SimpleNamespace(llm_config={}, retrieval_config={})
    with (
        patch("callobuzz_rag.core.config.get_settings", return_value=SETTINGS),
        patch.object(tasks, "_encryptor", return_value=None),
    ):
        _, resolved = tasks._resolve_llm(corpus, chosen)  # type: ignore[arg-type]
        _, plain = tasks._resolve_llm(corpus)  # type: ignore[arg-type]
    assert resolved.fast_model == "p/fast"
    assert plain.fast_model != "p/fast"


def test_setup_and_assistant_helpers_load_the_plan_profile() -> None:
    """The two helpers that call the knowledge base's model for the owner."""
    for path in ("conversation/setup/service.py", "api/routes/assistant.py"):
        src = (SRC / path).read_text(encoding="utf-8")
        assert "plan_profile" in src and "resolve_with_profile" in src, path


def test_the_exempt_cob_path_passes_no_profile() -> None:
    src = (SRC / "cob/orchestrator.py").read_text(encoding="utf-8")
    assert "build_director(" in src
    assert "profile=" not in src.split("build_director(", 1)[1].split(")", 1)[0]
