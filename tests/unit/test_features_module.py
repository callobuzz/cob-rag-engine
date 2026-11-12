"""api/features.py: flag parsing, gates and the pinned route tables (no database)."""

from typing import Any

import pytest
from pydantic import ValidationError

from callobuzz_rag.api.features import (
    FEATURE_OF_GATE,
    FIELD_GATES,
    ROUTE_GATES,
    DashboardFeatures,
    field_gates,
    full_features,
    hidden_gates,
    not_in_plan_message,
    parse_features,
    route_gate,
)
from callobuzz_rag.api.permissions import ROUTE_OPS


def test_advanced_off_hides_every_gate_but_widget_settings() -> None:
    assert hidden_gates({"advanced": False}) == {
        "models",
        "retrieval",
        "keys_agents",
        "webhooks",
        "quality",
    }


@pytest.mark.parametrize(
    ("raw", "hidden"),
    [
        (None, set()),
        ({}, set()),
        ({"widget_code_only": True}, {"widget_settings"}),
        ({"models": False, "bogus": 1}, {"models"}),
        ({"models": "no"}, set()),
        ({"advanced": False, "widget_code_only": True}, {
            "models", "retrieval", "keys_agents", "webhooks", "quality", "widget_settings"}),
        ({"api_docs": False, "usage_details": False}, set()),  # nav only, never a gate
    ],
)  # fmt: skip
def test_hidden_gates_parsing(raw: dict[str, Any] | None, hidden: set[str]) -> None:
    assert hidden_gates(raw) == frozenset(hidden)


def test_defaults_show_everything_and_do_not_restrict_the_widget() -> None:
    flags = parse_features(None)
    assert flags.model_dump() == {
        "advanced": True,
        "models": True,
        "retrieval": True,
        "keys_agents": True,
        "webhooks": True,
        "quality": True,
        "api_docs": True,
        "usage_details": True,
        "widget_code_only": False,
    }
    assert full_features({"quality": False})["quality"] is False


def test_the_strict_model_refuses_an_unknown_flag() -> None:
    with pytest.raises(ValidationError):
        DashboardFeatures(bogus=True)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("method", "path", "gate"),
    [
        ("POST", "/api/v1/tenants/me/keys", "keys_agents"),
        ("GET", "/api/v1/corpora/abc/webhooks", "webhooks"),
        ("POST", "/api/v1/corpora/abc/golden-sets/from-template", "quality"),
        ("POST", "/api/v1/golden/import", "quality"),  # beats /golden/{pair_id}
        ("POST", "/api/v1/migrations/m1/approve", "retrieval"),
        ("DELETE", "/api/v1/corpora/abc/widget-keys/k1", "widget_settings"),
        ("POST", "/api/v1/corpora/abc/widget-keys/k1/rotate/", "widget_settings"),
    ],
)
def test_route_gate_matches_templates(method: str, path: str, gate: str) -> None:
    assert route_gate(method, path) == gate


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/documents"),
        ("GET", "/api/v1/leads"),
        ("GET", "/api/v1/usage"),
        ("GET", "/api/v1/corpora/abc/eval/runs"),  # results stay readable
        ("GET", "/api/v1/handoffs"),
        ("POST", "/api/v1/query"),
        ("POST", "/api/v1/corpora/abc/widget-keys"),  # create is decided in the route
    ],
)
def test_data_routes_are_not_gated(method: str, path: str) -> None:
    assert route_gate(method, path) is None


def test_field_gates_lookup() -> None:
    assert field_gates("PUT", "/api/v1/corpora/abc") == {
        "llm_config": "models",
        "prompt_config": "models",
        "retrieval_config": "retrieval",
        "embedding_config": "retrieval",
    }
    assert field_gates("GET", "/api/v1/corpora/abc") == {}


def test_every_gated_route_keeps_its_permission_row() -> None:
    missing = {key for key in ROUTE_GATES if key not in ROUTE_OPS}
    assert not missing, missing


def test_gate_flags_are_real_dashboard_flags() -> None:
    assert set(FEATURE_OF_GATE.values()) <= set(DashboardFeatures.model_fields)


def test_message_names_the_support_contact_or_the_team() -> None:
    assert "help@shreedental.in" in not_in_plan_message("help@shreedental.in")
    assert "the team that set up your account" in not_in_plan_message(None)
    assert "corpus" not in not_in_plan_message(None).lower()


def test_every_gate_names_a_real_route() -> None:
    from callobuzz_rag.api.app import create_app

    paths = {(m.upper(), p) for p, ops in create_app().openapi()["paths"].items() for m in ops}
    assert set(ROUTE_GATES) <= paths, set(ROUTE_GATES) - paths
    assert set(FIELD_GATES) <= paths, set(FIELD_GATES) - paths


def test_field_gate_fields_exist_on_the_request_models() -> None:
    from callobuzz_rag.api.app import create_app

    spec = create_app().openapi()
    schemas = spec["components"]["schemas"]
    for (method, path), fields in FIELD_GATES.items():
        body = spec["paths"][path][method.lower()]["requestBody"]["content"]["application/json"]
        ref = body["schema"]["$ref"].rsplit("/", 1)[-1]
        props = schemas[ref]["properties"]
        assert set(fields) <= set(props), (method, path, set(fields) - set(props))
