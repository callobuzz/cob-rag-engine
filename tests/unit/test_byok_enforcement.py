"""P5 T3: an own AI key is used only where the plan (or an active add-on) includes it.

What is pinned: the key save routes refuse it (403 `byok_not_in_plan`) while removing a key and
saving other settings still work; the answer resolver drops a stored key, its model choices and a
per-request key, so the payer becomes the operator; a fair-use downgrade keeps the allowance; every
`payer_hint` call site passes it; COB, the Content Portal and the consumption view follow. The
lookup itself is in `test_byok_allowed.py` and `test_tiers_profiles.py`.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing import byok, enforcement
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver
from callobuzz_rag.core.secrets import FernetEncryptor, generate_fernet_key
from callobuzz_rag.tiers.profiles import noop_profile
from callobuzz_rag.tiers.resolve import resolve_with_profile
from tests.unit.scope_harness import call, scoped_app

FERNET = generate_fernet_key()
SETTINGS = Settings(
    _env_file=None,
    admin_api_key="test-admin-key",
    llm_api_key="sk-engine",
    llm_model="engine/model",
    encryption_key=FERNET,
)
STORED = {
    "provider": "anthropic",
    "model": "anthropic/own-model",
    "api_key_encrypted": FernetEncryptor(FERNET).encrypt("sk-own"),
    "temperature": 0.2,
}
ALLOWED = noop_profile()
DENIED = replace(ALLOWED, byok_allowed=False)


def resolve(profile: Any, *, config: Any = STORED, headers: Any = None) -> Any:
    resolver = ConfigResolver(SETTINGS, encryptor=FernetEncryptor(FERNET))
    corpus = SimpleNamespace(llm_config=config, retrieval_config=None)
    llm, _ = resolve_with_profile(
        resolver, corpus, headers=headers, profile=profile, settings=SETTINGS
    )
    return llm


# ------------------------------------------------------------------- the answer resolver


def test_a_stored_key_pays_when_the_plan_allows_it() -> None:
    llm = resolve(ALLOWED)
    assert (llm.payer, llm.api_key, llm.model) == ("byok", "sk-own", "anthropic/own-model")


def test_without_the_allowance_the_operators_key_and_model_answer() -> None:
    llm = resolve(DENIED)
    assert llm.payer == "operator"
    assert llm.api_key == "sk-engine" and llm.model == SETTINGS.llm_model
    assert llm.temperature == 0.2  # the business's harmless settings stay
    assert STORED["api_key_encrypted"] and STORED["model"]  # and the stored row is untouched


def test_a_model_picked_on_the_operators_key_survives_when_no_own_key_is_stored() -> None:
    config = {"model": "engine/picked", "fast_model": "engine/fast", "temperature": 0.2}
    llm = resolve(DENIED, config=config)
    assert (llm.payer, llm.api_key, llm.model) == ("operator", "sk-engine", "engine/picked")


def test_a_key_sent_with_the_request_is_ignored_not_refused() -> None:
    headers = {"x-llm-api-key": "sk-header", "x-llm-base-url": "https://evil.example"}
    assert resolve(ALLOWED, config=None, headers=headers).payer == "byok"
    llm = resolve(DENIED, config=None, headers=headers)
    assert (llm.payer, llm.api_key) == ("operator", "sk-engine")
    assert llm.base_url != "https://evil.example"


# ------------------------------------------------------------------- the fair-use downgrade


async def test_a_downgrade_keeps_the_plans_own_key_answer() -> None:
    from callobuzz_rag.fairuse.usage import FairUseUsage
    from tests.unit.test_fairuse_gate import FREE, STD, effective, env, run

    over = FairUseUsage(questions_per_hour=31)
    for plan_flag in (False, True):
        with env(effective({"questions_per_hour": 30}), usage=over, target=FREE):
            decision = await run(plan=replace(STD, byok_allowed=plan_flag))
        assert decision.action == "downgrade"
        assert decision.profile.byok_allowed is plan_flag


def test_a_downgraded_answer_of_a_plan_without_the_allowance_stays_on_the_operators_key() -> None:
    from callobuzz_rag.tiers.profiles import downgraded

    llm = resolve(downgraded(DENIED))
    assert (llm.payer, llm.api_key, llm.model) == ("operator", "sk-engine", SETTINGS.llm_model)


# ------------------------------------------------------------------- the key save routes


@contextmanager
def allowance(value: bool) -> Iterator[AsyncMock]:
    with (
        patch.object(byok, "byok_allowed", AsyncMock(return_value=value)) as lookup,
        patch.object(byok, "plans_with_byok", AsyncMock(return_value=["Pro"])),
    ):
        yield lookup


async def put(app: Any, key: str, corpus: Any, llm_config: Any) -> Any:
    return await call(
        app, key, "PUT", f"/api/v1/corpora/{corpus.id}", json={"llm_config": llm_config}
    )


@pytest.mark.parametrize("key", ["admin", "tk_a"])
async def test_saving_a_key_without_the_allowance_is_refused_and_writes_nothing(key: str) -> None:
    with scoped_app() as (app, world), allowance(False):
        res = await put(app, key, world.corpus_a, {"api_key": "sk-new", "model": "openai/x"})
    assert res.status_code == 403, res.text
    body = res.json()
    assert body["code"] == "byok_not_in_plan"
    assert "Pro" in body["detail"] and "add-on" in body["detail"]
    assert world.corpus_a.llm_config is None


def test_the_refusal_names_the_plans_that_include_it_or_none() -> None:
    assert "Pro and Business" in byok.not_in_plan_message("help@x.in", ["Pro", "Business"])
    none = byok.not_in_plan_message("help@x.in", [])
    assert "Pro" not in none and "help@x.in" in none and "add-on" in none


async def test_removing_a_key_and_other_settings_still_save() -> None:
    with scoped_app() as (app, world), allowance(False):
        world.corpus_a.llm_config = {"api_key_encrypted": "kept", "temperature": 0.5}
        gone = await put(app, "tk_a", world.corpus_a, {"api_key": None})
        assert gone.status_code == 200, gone.text
        assert world.corpus_a.llm_config.get("api_key_encrypted") is None
        warm = await put(app, "tk_a", world.corpus_a, {"temperature": 0.9})
        assert warm.status_code == 200, warm.text
        assert world.corpus_a.llm_config["temperature"] == 0.9


async def test_with_the_plan_flag_or_an_active_grant_a_key_is_saved_as_today() -> None:
    with scoped_app() as (app, world), allowance(True) as lookup:
        app.state.settings.encryption_key = FERNET
        res = await put(app, "tk_a", world.corpus_a, {"api_key": "sk-new"})
    assert res.status_code == 200, res.text
    assert world.corpus_a.llm_config["api_key_encrypted"] not in (None, "sk-new")
    assert lookup.await_args.args[1] == world.corpus_a.tenant_id


async def test_creating_a_knowledge_base_with_a_key_is_refused_too() -> None:
    with scoped_app() as (app, _), allowance(False):
        res = await call(
            app,
            "tk_a",
            "POST",
            "/api/v1/corpora",
            json={"name": "New", "llm_config": {"api_key": "sk-new"}},
        )
    assert res.status_code == 403, res.text
    assert res.json()["code"] == "byok_not_in_plan"


# ------------------------------------------------------------------- the payer at every gate


def test_payer_hint_needs_the_allowance_keyword() -> None:
    corpus = SimpleNamespace(llm_config=STORED)
    with pytest.raises(TypeError):
        enforcement.payer_hint(corpus, None, use_headers=False)  # type: ignore[call-arg]
    assert enforcement.payer_hint(corpus, None, use_headers=False, byok_allowed=True) == "byok"
    assert enforcement.payer_hint(corpus, None, use_headers=False, byok_allowed=False) == "operator"


def gate_spy(seen: list[Any]) -> AsyncMock:
    async def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["payer"])
        raise APIError(418, "stop here", "gate_spy")

    return AsyncMock(side_effect=spy)


@pytest.mark.parametrize(("profile", "expected"), [(ALLOWED, "byok"), (DENIED, "operator")])
async def test_the_conversation_gate_gets_the_plans_payer(
    profile: Any, expected: str, tenant_profile: Any
) -> None:
    tenant_profile.profile = profile
    seen: list[Any] = []
    with (
        scoped_app() as (app, world),
        patch("callobuzz_rag.api.routes.conversations.answer_gate", gate_spy(seen)),
    ):
        world.corpus_a.llm_config = dict(STORED)
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/conversations/{uuid.uuid4()}/messages",
            json={"content": "hi"},
        )
    assert res.status_code == 418, res.text
    assert seen == [expected]


@pytest.mark.parametrize(("profile", "expected"), [(ALLOWED, "byok"), (DENIED, "operator")])
async def test_the_widget_gate_gets_the_plans_payer(
    profile: Any, expected: str, tenant_profile: Any
) -> None:
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.widget.security import LimitResult
    from tests.unit.test_widget import SITE, _client, _key, _session, _settings, _use_session

    tenant_profile.profile = profile
    app = create_app()
    app.state.settings = _settings()
    app.state.widget_rate_limiter = SimpleNamespace(check=AsyncMock(return_value=LimitResult(True)))
    corpus = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        qdrant_collection="c",
        retrieval_config=None,
        llm_config=dict(STORED),
        prompt_config=None,
    )
    _use_session(app, _session(_key(corpus.id), corpus))
    seen: list[Any] = []
    with patch("callobuzz_rag.api.routes.widget.answer_gate", gate_spy(seen)):
        async with await _client(app) as c:
            res = await c.post(
                "/api/v1/widget/chat",
                json={"message": "Can I return this?"},
                headers={"X-Widget-Key": "pk_x", "Origin": SITE},
            )
    assert res.status_code == 418, res.text
    assert seen == [expected]


@pytest.mark.parametrize(("profile", "expected"), [(ALLOWED, "byok"), (DENIED, "operator")])
async def test_a_golden_run_gets_the_plans_payer(
    profile: Any, expected: str, tenant_profile: Any
) -> None:
    from callobuzz_rag.api.routes.evals import _check_question_room

    tenant_profile.profile = profile
    seen: list[Any] = []
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SETTINGS)))
    corpus = SimpleNamespace(tenant_id=uuid.uuid4(), llm_config=dict(STORED))
    with (
        patch("callobuzz_rag.fairuse.gate.answer_gate", gate_spy(seen)),
        pytest.raises(APIError),
    ):
        await _check_question_room(MagicMock(), request, corpus, 3)  # type: ignore[arg-type]
    assert seen == [expected]


@pytest.mark.assistant_on
@pytest.mark.parametrize(("profile", "expected"), [(ALLOWED, "byok"), (DENIED, "operator")])
async def test_a_whatsapp_answer_gets_the_plans_payer(
    profile: Any, expected: str, tenant_profile: Any
) -> None:
    from callobuzz_rag.fairuse import gate
    from tests.unit.whatsapp_fixtures import inbound_text
    from tests.unit.whatsapp_rig import rig_for
    from tests.unit.whatsapp_world import WhatsAppWorld

    tenant_profile.profile = profile
    world = WhatsAppWorld()
    world.corpus.llm_config = dict(STORED)
    seen: list[Any] = []
    real = gate.answer_gate

    async def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["payer"])
        return await real(*args, **kwargs)

    with rig_for(world) as rig, patch("callobuzz_rag.fairuse.gate.answer_gate", spy):
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.BYOK1", text="Price?"))
        await rig.run(thread_id)
    assert seen == [expected]


# ------------------------------------------------------------------- the other readers of a key


async def test_cob_skips_a_stored_key_the_plan_does_not_include() -> None:
    from callobuzz_rag.cob.llm_choice import cob_llm_config
    from callobuzz_rag.cob.types import CobCaller, CobRole

    tenant_id = uuid.uuid4()
    corpus = SimpleNamespace(llm_config=dict(STORED))
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [corpus]
    session = SimpleNamespace(execute=AsyncMock(return_value=rows))
    caller = MagicMock(spec=CobCaller)
    caller.role, caller.tenant_id = CobRole.OWNER, tenant_id
    from callobuzz_rag.cob import llm_choice

    with allowance_in(llm_choice, False):
        assert await cob_llm_config(session, caller) is None
    with allowance_in(llm_choice, True):
        assert (await cob_llm_config(session, caller)) == STORED


def test_the_content_portal_uses_the_engine_key_without_the_allowance() -> None:
    from callobuzz_rag.content_portal.services.ai_transformer import _resolve_llm_config

    corpus = SimpleNamespace(llm_config={**STORED, "model": "anthropic/own-model"})
    own = _resolve_llm_config(corpus, SETTINGS, None, byok_allowed=True)
    assert own[1] == "sk-own"
    model, key = _resolve_llm_config(corpus, SETTINGS, None, byok_allowed=False)
    assert key == "sk-engine" and model != "anthropic/own-model"
    assert corpus.llm_config["api_key_encrypted"] == STORED["api_key_encrypted"]  # kept


@pytest.mark.parametrize(("allowed", "view"), [(True, "byok"), (False, "operator_key")])
async def test_the_consumption_view_does_not_call_an_unused_key_byok(
    allowed: bool, view: str
) -> None:
    from datetime import date

    from callobuzz_rag.analytics import consumption

    rows = [
        {
            "key": "byok",
            "requests": 4,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "reasoning_tokens": 0,
            "cost_usd": 0.001,
            "unpriced_requests": 0,
        }
    ]
    with (
        patch.object(consumption, "usage_report", AsyncMock(return_value=rows)),
        allowance_in(consumption, allowed),
        patch.object(consumption, "question_quota", AsyncMock(return_value=None)),
        patch.object(consumption, "questions_this_month", AsyncMock(return_value=4)),
    ):
        got = await consumption.tenant_consumption(MagicMock(), uuid.uuid4(), date(2026, 10, 15))
    assert got["view"] == view


def allowance_in(module: Any, value: bool) -> Any:
    return patch.object(module, "byok_allowed", AsyncMock(return_value=value))


@pytest.mark.parametrize(("allowed", "payer_key"), [(True, "sk-own"), (False, "sk-engine")])
async def test_the_ragas_judge_uses_the_engine_key_without_the_allowance(
    allowed: bool, payer_key: str
) -> None:
    from callobuzz_rag.evaluation.runner import EvaluationRunner

    corpus = SimpleNamespace(llm_config=dict(STORED), tenant_id=uuid.uuid4())
    session = SimpleNamespace(get=AsyncMock(return_value=corpus))
    runner = EvaluationRunner(MagicMock(), settings=SETTINGS)
    with allowance_in(byok, allowed):
        llm = await runner._resolve_llm_config(session, uuid.uuid4())  # type: ignore[arg-type]
    assert llm.api_key == payer_key
    assert corpus.llm_config["api_key_encrypted"] == STORED["api_key_encrypted"]
