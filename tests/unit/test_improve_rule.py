"""'Improve this rule': one fast-model JSON call, suggestions only, metered (C9)."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.conversation.improve import improve_rule
from callobuzz_rag.conversation.schema import GuidanceRule
from callobuzz_rag.core.exceptions import LLMError
from tests.unit.scope_harness import MATRIX, call, check, expected, scoped_app
from tests.unit.test_assistant_routes import _row

pytestmark = pytest.mark.usefixtures("unlimited_plan")

ROUTE = "callobuzz_rag.api.routes.assistant."
PROVIDER = "callobuzz_rag.llm.litellm_provider.LiteLLMProvider.from_resolved_config"

OTHERS = [
    GuidanceRule(
        id="fees-exact", type="compliance", instruction="Quote fees only from the fee list."
    ),
    GuidanceRule(id="no-diagnosis", type="compliance", instruction="Never interpret test results."),
]


def _llm(content: Any = None, *, error: Exception | None = None) -> SimpleNamespace:
    complete = AsyncMock(
        return_value=SimpleNamespace(content=json.dumps(content)), side_effect=error
    )
    return SimpleNamespace(
        has_credentials=True,
        fast_call_options=lambda: {"model": "fast"},
        complete_json=complete,
    )


async def test_returns_rewrite_and_known_conflicts_only() -> None:
    llm = _llm(
        {
            "improved": "If someone asks about results, say a doctor will explain them.",
            "conflicts": [
                {"rule_id": "no-diagnosis", "reason": "Both cover results; keep them consistent."},
                {"rule_id": "invented", "reason": "x"},
            ],
            "notes": "Made it one objective.",
        }
    )
    out = await improve_rule(
        llm, instruction="tell ppl results r fine if normal", rule_type="compliance", others=OTHERS
    )
    assert out.improved.startswith("If someone asks")
    assert [c.rule_id for c in out.conflicts] == ["no-diagnosis"]
    sent = llm.complete_json.await_args.args[0]
    assert "fees-exact" in sent[-1].content and "tell ppl results" in sent[-1].content
    assert "same language and script" in sent[0].content
    assert llm.complete_json.await_args.kwargs == {"model": "fast"}


async def test_context_is_bounded_to_forty_enabled_rules_of_200_characters() -> None:
    many = [
        GuidanceRule(id=f"r{i}", type="context", instruction="x" * 300, enabled=i % 2 == 0)
        for i in range(120)
    ]
    llm = _llm({"improved": "ok", "conflicts": [], "notes": ""})
    await improve_rule(llm, instruction="be brief", rule_type="communication", others=many)
    user = llm.complete_json.await_args.args[0][-1].content
    assert user.count("\n- r") == 40 and "x" * 201 not in user


async def test_bad_json_keeps_the_original() -> None:
    out = await improve_rule(
        _llm([]), instruction="Be brief.", rule_type="communication", others=[]
    )
    assert out.improved == "Be brief." and out.conflicts == [] and out.notes


async def test_a_model_error_is_raised_for_the_route() -> None:
    with pytest.raises(LLMError):
        await improve_rule(
            _llm(error=LLMError("boom")), instruction="x", rule_type="sales", others=[]
        )


def _runtime(cid: Any, **config: object) -> SimpleNamespace:
    row = _row(cid, 3, "published", **config)
    from callobuzz_rag.conversation.schema import AssistantConfig

    return SimpleNamespace(
        config=AssistantConfig.model_validate(row.config),
        version=3,
        draft_revision=None,
        enabled=True,
    )


async def test_route_without_llm_credential_is_503() -> None:
    with scoped_app() as (app, world), patch(PROVIDER) as provider:
        provider.return_value = SimpleNamespace(has_credentials=False)
        cid = world.corpus("a").id
        res = await call(
            app,
            "member_a",
            "POST",
            f"/api/v1/corpora/{cid}/assistant/rules/improve",
            json={"instruction": "be nice", "type": "communication"},
        )
    assert res.status_code == 503 and res.json()["code"] == "assistant_llm_unavailable"


async def test_route_suggests_using_the_draft_rules_and_meters_the_spend() -> None:
    llm = _llm({"improved": "Be brief and polite.", "conflicts": [], "notes": "ok"})
    usage = AsyncMock()
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        runtime = _runtime(
            cid,
            rules=[
                {
                    "id": "fees-exact",
                    "type": "compliance",
                    "instruction": "Quote fees only from the fee list.",
                }
            ],
        )
        with (
            patch(PROVIDER, return_value=llm),
            patch(ROUTE + "get_runtime_config", AsyncMock(return_value=runtime)) as runtime_of,
            patch(ROUTE + "record_usage", usage),
            patch(ROUTE + "request_cost_of", return_value="COST"),
        ):
            res = await call(
                app,
                "member_a",
                "POST",
                f"/api/v1/corpora/{cid}/assistant/rules/improve",
                json={"instruction": "be brif", "type": "communication"},
            )
    assert res.status_code == 200, res.text
    assert res.json()["improved"] == "Be brief and polite."
    assert runtime_of.await_args.kwargs == {"use_draft": True}
    assert "fees-exact" in llm.complete_json.await_args.args[0][-1].content
    kwargs = usage.await_args.kwargs
    assert kwargs["source"] == "assistant" and kwargs["requests"] == 0
    assert kwargs["cost"] == "COST"


async def test_route_model_failure_is_a_502_and_still_metered() -> None:
    usage = AsyncMock()
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        with (
            patch(PROVIDER, return_value=_llm(error=LLMError("boom"))),
            patch(ROUTE + "get_runtime_config", AsyncMock(return_value=_runtime(cid))),
            patch(ROUTE + "record_usage", usage),
            patch(ROUTE + "request_cost_of", return_value="COST"),
        ):
            res = await call(
                app,
                "member_a",
                "POST",
                f"/api/v1/corpora/{cid}/assistant/rules/improve",
                json={"instruction": "be brif", "type": "communication"},
            )
    assert res.status_code == 502 and res.json()["code"] == "assistant_llm_failed"
    usage.assert_awaited_once()


async def test_route_rejects_unknown_fields() -> None:
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        res = await call(
            app,
            "member_a",
            "POST",
            f"/api/v1/corpora/{cid}/assistant/rules/improve",
            json={"instruction": "x", "type": "communication", "apply": True},
        )
    assert res.status_code == 422


@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_scoping(key: str, which: str, outcome: str) -> None:
    llm = _llm({"improved": "x", "conflicts": [], "notes": ""})
    with (
        scoped_app() as (scoped, world),
        patch(PROVIDER, return_value=llm),
        patch(ROUTE + "get_runtime_config", AsyncMock(return_value=None)),
        patch(ROUTE + "record_usage", AsyncMock()),
    ):
        url = f"/api/v1/corpora/{world.corpus(which).id}/assistant/rules/improve"
        body = {"instruction": "x", "type": "communication"}
        res = await call(scoped, key, "POST", url, json=body)
    check(expected(key, outcome, "POST", url, body), res.status_code, res.text)


@pytest.mark.parametrize(("key", "admin"), [("sa", True), ("admin", True), ("member_a", False)])
async def test_the_operators_helper_call_is_metered_waived(key: str, admin: bool) -> None:
    from callobuzz_rag.billing.context import ADMIN_ANSWER

    seen: list[bool] = []

    async def usage(*args: Any, **kwargs: Any) -> None:
        seen.append(ADMIN_ANSWER.get())

    llm = _llm({"improved": "Be brief.", "conflicts": [], "notes": "ok"})
    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        with (
            patch(PROVIDER, return_value=llm),
            patch(ROUTE + "get_runtime_config", AsyncMock(return_value=_runtime(cid))),
            patch(ROUTE + "record_usage", usage),
            patch(ROUTE + "request_cost_of", return_value="COST"),
        ):
            ADMIN_ANSWER.set(not admin)
            res = await call(
                app,
                key,
                "POST",
                f"/api/v1/corpora/{cid}/assistant/rules/improve",
                json={"instruction": "be brif", "type": "communication"},
            )
    assert res.status_code == 200, res.text
    assert seen == [admin]
