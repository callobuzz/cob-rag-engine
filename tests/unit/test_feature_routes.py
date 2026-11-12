"""Plan features (BIZ3): a hidden feature's configuration routes refuse a business's sessions.

Owner and member sessions of a business are gated; API keys, the admin principal and the
deployer's own workspace never are; data routes never are.
"""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.api.features import FEATURE_OF_GATE
from callobuzz_rag.db.models.widget_key import WidgetKey
from tests.unit.scope_harness import call, scoped_app

KEY_ID = uuid.uuid4()
MIGRATION_ID = uuid.uuid4()

GATED = [
    ("POST", "/api/v1/tenants/me/keys", {"name": "Zapier", "permission": "read"}, "keys_agents"),
    ("GET", "/api/v1/corpora/{corpus_id}/webhooks", None, "webhooks"),
    ("POST", "/api/v1/corpora/{corpus_id}/golden-sets", {"name": "Clinic basics"}, "quality"),
    ("POST", "/api/v1/models/discover/openrouter", None, "models"),
    ("POST", "/api/v1/migrations/{migration_id}/approve", None, "retrieval"),
    ("POST", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}/rotate", None, "widget_settings"),
]


def features_hiding(gate: str) -> dict[str, bool]:
    return {"widget_code_only": True} if gate == "widget_settings" else {gate: False}


class Harness:
    def __init__(self, app: Any, world: Any) -> None:
        self.app, self.world = app, world

    def set_plan_features(self, tenant: str, features: dict[str, Any]) -> None:
        owner = {"tenant_a": self.world.tenant_a, "tenant_b": self.world.tenant_b}[tenant]
        self.world.plan_features[owner.id] = features

    async def call(
        self,
        who: str,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        path = path.format(
            corpus_id=self.world.corpus_a.id, key_id=KEY_ID, migration_id=MIGRATION_ID
        )
        kwargs: dict[str, Any] = {"json": body} if body is not None else {}
        if headers:
            kwargs["headers"] = headers
        try:
            return await call(self.app, who, method, path, **kwargs)
        except Exception as exc:
            # The route got past the gate and the in-memory database could not finish it.
            return SimpleNamespace(
                status_code=500, text=repr(exc), json=lambda: {"code": "route_crashed"}
            )


@pytest.fixture
def h() -> Any:
    with scoped_app() as (app, world):
        yield Harness(app, world)


def code_of(res: Any) -> str | None:
    try:
        return res.json().get("code")  # type: ignore[no-any-return]
    except Exception:
        return None


@pytest.mark.parametrize(("method", "path", "body", "gate"), GATED)
async def test_hidden_gate_refuses_owner_and_member_sessions(
    h: Harness, method: str, path: str, body: Any, gate: str
) -> None:
    h.set_plan_features("tenant_a", features_hiding(gate))
    for who in ("owner_a", "member_a"):
        r = await h.call(who, method, path, body)
        assert r.status_code == 403, (who, r.text)
        assert r.json()["code"] == "feature_not_in_plan"
        assert r.json()["feature"] == FEATURE_OF_GATE[gate]
        assert "plan" in r.json()["detail"]


@pytest.mark.parametrize(("method", "path", "body", "gate"), GATED)
@pytest.mark.parametrize("who", ["tk_a", "ck_a", "admin", "sa", "operator_owner"])
async def test_keys_admin_and_operator_are_never_gated(
    h: Harness, method: str, path: str, body: Any, gate: str, who: str
) -> None:
    h.set_plan_features("tenant_a", features_hiding(gate))
    r = await h.call(who, method, path, body)
    assert code_of(r) != "feature_not_in_plan"


@pytest.mark.parametrize(("method", "path", "body", "gate"), GATED)
async def test_other_workspaces_are_not_affected(
    h: Harness, method: str, path: str, body: Any, gate: str
) -> None:
    h.set_plan_features("tenant_a", features_hiding(gate))
    r = await h.call("tk_b", method, path, body)  # a different workspace's key
    assert code_of(r) != "feature_not_in_plan"


async def test_a_plan_with_nothing_hidden_changes_nothing(h: Harness) -> None:
    for method, path, body, _gate in GATED:
        r = await h.call("owner_a", method, path, body)
        assert code_of(r) != "feature_not_in_plan", (method, path)


async def test_advanced_off_refuses_the_technical_routes(h: Harness) -> None:
    h.set_plan_features("tenant_a", {"advanced": False})
    for method, path, body, gate in GATED:
        r = await h.call("owner_a", method, path, body)
        if gate == "widget_settings":
            assert code_of(r) != "feature_not_in_plan"  # only widget_code_only hides it
        else:
            assert code_of(r) == "feature_not_in_plan", (method, path)


async def test_field_gates_on_knowledge_base_update(h: Harness) -> None:
    h.set_plan_features("tenant_a", {"models": False, "retrieval": False})
    ok = await h.call("owner_a", "PUT", "/api/v1/corpora/{corpus_id}", {"name": "Shree Dental"})
    assert ok.status_code == 200, ok.text
    r = await h.call(
        "owner_a", "PUT", "/api/v1/corpora/{corpus_id}", {"llm_config": {"model": "gpt-4o-mini"}}
    )
    assert r.status_code == 403 and r.json()["feature"] == "models"
    r = await h.call(
        "member_a", "PUT", "/api/v1/corpora/{corpus_id}", {"retrieval_config": {"top_k": 8}}
    )
    assert r.status_code == 403 and r.json()["feature"] == "retrieval"
    # a field the plan still shows passes the gate
    r = await h.call("owner_a", "PUT", "/api/v1/corpora/{corpus_id}", {"description": "Pune"})
    assert code_of(r) != "feature_not_in_plan"


async def test_field_gates_on_create_and_config_patch(h: Harness) -> None:
    h.set_plan_features("tenant_a", {"models": False})
    r = await h.call(
        "owner_a", "POST", "/api/v1/corpora", {"name": "X", "llm_config": {"model": "gpt-4o-mini"}}
    )
    assert r.status_code == 403 and r.json()["feature"] == "models"
    r = await h.call(
        "owner_a", "PATCH", "/api/v1/config?corpus_id={corpus_id}", {"prompt_config": {}}
    )
    assert r.status_code == 403 and r.json()["feature"] == "models"
    r = await h.call(
        "tk_a", "PATCH", "/api/v1/config?corpus_id={corpus_id}", {"prompt_config": {}}
    )  # keys: never
    assert code_of(r) != "feature_not_in_plan"


async def test_widget_code_only_allows_one_key_and_origin_edits(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from callobuzz_rag.api.routes import widget_keys

    h.set_plan_features("tenant_a", {"widget_code_only": True})
    existing = {"n": 0}

    async def key_count(_session: Any, _corpus_id: Any) -> int:
        return existing["n"]

    monkeypatch.setattr(widget_keys, "_key_count", key_count)
    body = {"name": "shreedental.in", "allowed_origins": ["https://shreedental.in"]}
    first = await h.call("owner_a", "POST", "/api/v1/corpora/{corpus_id}/widget-keys", body)
    # the first key is allowed (the in-memory database cannot finish the response itself)
    assert code_of(first) != "feature_not_in_plan", first.text
    existing["n"] = 1
    again = await h.call(
        "owner_a",
        "POST",
        "/api/v1/corpora/{corpus_id}/widget-keys",
        {"name": "second", "allowed_origins": ["https://shreedental.in"]},
    )
    assert again.status_code == 403 and again.json()["code"] == "feature_not_in_plan"
    assert again.json()["feature"] == "widget_code_only"

    key = WidgetKey(
        id=KEY_ID,
        corpus_id=h.world.corpus_a.id,
        name="shreedental.in",
        public_key="pk_x",
        allowed_origins=["https://shreedental.in"],
        rate_limit_per_minute=30,
        daily_message_limit=500,
        appearance={},
        is_active=True,
    )
    key.created_at = h.world.tenant_a.created_at
    h.world.add_row(WidgetKey, key)
    path = "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}"
    ok = await h.call(
        "owner_a",
        "PATCH",
        path,
        {"allowed_origins": ["https://shreedental.in", "https://www.shreedental.in"]},
    )
    assert ok.status_code == 200, ok.text
    no = await h.call("owner_a", "PATCH", path, {"rate_limit_per_minute": 5})
    assert no.status_code == 403 and no.json()["code"] == "feature_not_in_plan"
    renamed = await h.call("owner_a", "PATCH", path, {"name": "Other"})
    assert renamed.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/documents"),
        ("GET", "/api/v1/leads"),
        ("GET", "/api/v1/usage"),
        ("GET", "/api/v1/corpora/{corpus_id}/eval/runs"),
        ("GET", "/api/v1/handoffs"),
    ],
)
async def test_data_routes_are_never_gated(h: Harness, method: str, path: str) -> None:
    h.set_plan_features(
        "tenant_a",
        {
            "advanced": False,
            "usage_details": False,
            "api_docs": False,
            "widget_code_only": True,
            "quality": False,
        },
    )
    r = await h.call("owner_a", method, path, None)
    assert code_of(r) != "feature_not_in_plan"


async def test_info_reports_the_plans_flags_to_a_business_session(h: Harness) -> None:
    h.set_plan_features("tenant_a", {"models": False, "widget_code_only": True})
    r = await h.call("owner_a", "GET", "/api/v1/info")
    assert r.status_code == 200, r.text
    dash = r.json()["features"]["dashboard"]
    assert dash["models"] is False and dash["widget_code_only"] is True
    assert dash["advanced"] is True and dash["webhooks"] is True  # defaults filled in
    for who in ("tk_a", "admin", "sa", "operator_owner"):
        again = await h.call(who, "GET", "/api/v1/info")
        assert again.json()["features"]["dashboard"] is None, who


@pytest.mark.parametrize(
    "header", ["X-LLM-Model", "X-LLM-Provider", "X-LLM-API-Key", "X-LLM-Base-URL"]
)
async def test_models_hidden_refuses_a_per_request_model_choice(h: Harness, header: str) -> None:
    h.set_plan_features("tenant_a", {"models": False})
    for who in ("owner_a", "member_a"):
        for path in ("/api/v1/query", "/api/v1/query/stream"):
            r = await h.call(who, "POST", path, {"query": "hours?"}, headers={header: "x"})
            assert r.status_code == 403, (who, path, r.text)
            assert r.json()["code"] == "feature_not_in_plan"
            assert r.json()["feature"] == FEATURE_OF_GATE["models"]


async def test_models_hidden_still_answers_without_the_headers(h: Harness) -> None:
    h.set_plan_features("tenant_a", {"models": False})
    r = await h.call("owner_a", "POST", "/api/v1/query", {"query": "hours?"})
    assert code_of(r) != "feature_not_in_plan"


async def test_models_shown_accepts_the_headers_and_keys_are_not_gated(h: Harness) -> None:
    r = await h.call(
        "owner_a", "POST", "/api/v1/query", {"query": "x"}, headers={"X-LLM-Model": "m"}
    )
    assert code_of(r) != "feature_not_in_plan"
    h.set_plan_features("tenant_a", {"models": False})
    r = await h.call("tk_a", "POST", "/api/v1/query", {"query": "x"}, headers={"X-LLM-Model": "m"})
    assert code_of(r) != "feature_not_in_plan"
