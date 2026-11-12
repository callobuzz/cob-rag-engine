"""Which dashboard features a plan shows a business, and which routes that hides.

The deployer decides per plan (`plans.dashboard_features`, JSONB) what a business's people
see. A hidden feature is gone from the dashboard menu **and** its configuration routes
refuse that business's owner and member sessions (`feature_not_in_plan`, 403). Data routes
(documents, leads, conversations, usage, queries, evaluation results) are never gated.

Who is gated: tenant **sessions** of a non-operator workspace only. API keys (`tk_`, `ck_`,
`pk_`), the admin principal and the operator workspace are never gated: keys are the
developer's API, and the deployer's own view is unaffected.

A new configuration route must be added to `ROUTE_GATES` (or `FIELD_GATES` for a body field),
or be a data route. `tests/unit/test_feature_routes.py` checks every entry names a real route.
"""

import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel

from callobuzz_rag.api.errors import APIError, error_response
from callobuzz_rag.api.permissions import _pattern, _specificity
from callobuzz_rag.core.schemas import StrictRequest

if TYPE_CHECKING:
    from fastapi import Request
    from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

DashboardFeature = Literal[
    "advanced",
    "models",
    "retrieval",
    "keys_agents",
    "webhooks",
    "quality",
    "api_docs",
    "usage_details",
    "widget_code_only",
]
Gate = Literal["models", "retrieval", "keys_agents", "webhooks", "quality", "widget_settings"]


class DashboardFeatures(StrictRequest):
    """What a plan shows a business. Every flag means "shown" (default true) except
    `widget_code_only` (default false, true = restricted)."""

    advanced: bool = True  # False hides the whole Advanced group (and every gate under it)
    models: bool = True  # answer models, thinking, language routing, BYOK keys
    retrieval: bool = True  # retrieval tuning, embeddings, migrations
    keys_agents: bool = True  # API keys (tk_/ck_), agent keys, MCP page
    webhooks: bool = True
    quality: bool = True  # golden sets, evaluation runs
    api_docs: bool = True  # API reference topics + Agents page (nav only)
    usage_details: bool = True  # raw usage tables (nav only; Home still shows plan usage)
    widget_code_only: bool = False  # True: the widget page shows the snippet + allowed sites only


# The same flags, as the dashboard reads them from `GET /api/v1/info`.
DashboardFeaturesInfo = DashboardFeatures

_DEFAULTS: Final[dict[str, bool]] = {
    name: bool(field.default) for name, field in DashboardFeatures.model_fields.items()
}

# The gate a flag controls; `advanced=False` hides every one of these except widget_settings.
_FLAG_GATES: Final[dict[str, Gate]] = {
    "models": "models",
    "retrieval": "retrieval",
    "keys_agents": "keys_agents",
    "webhooks": "webhooks",
    "quality": "quality",
}
FEATURE_OF_GATE: Final[dict[str, str]] = {
    "models": "models",
    "retrieval": "retrieval",
    "keys_agents": "keys_agents",
    "webhooks": "webhooks",
    "quality": "quality",
    "widget_settings": "widget_code_only",
}


def parse_features(raw: Mapping[str, Any] | None) -> DashboardFeatures:
    """The stored flags, leniently: unknown keys are ignored with a warning, a value that
    is not a boolean falls back to that flag's default. Never raises."""
    values = dict(_DEFAULTS)
    if raw:
        for key, value in raw.items():
            if key not in _DEFAULTS:
                logger.warning("Ignoring unknown dashboard feature %r on a plan", key)
            elif isinstance(value, bool):
                values[key] = value
            else:
                logger.warning("Dashboard feature %r must be true or false; using the default", key)
    return DashboardFeatures(**values)


def full_features(raw: Mapping[str, Any] | None) -> dict[str, bool]:
    """Every flag with defaults filled in (what plan responses and `/info` return)."""
    return parse_features(raw).model_dump()


def hidden_gates(features: Mapping[str, Any] | None) -> frozenset[Gate]:
    """The gates a plan hides. `advanced=False` hides every gate except `widget_settings`,
    which only `widget_code_only=True` hides."""
    flags = parse_features(features)
    hidden: set[Gate] = set()
    for flag, gate in _FLAG_GATES.items():
        if not flags.advanced or not getattr(flags, flag):
            hidden.add(gate)
    if flags.widget_code_only:
        hidden.add("widget_settings")
    return frozenset(hidden)


# --------------------------------------------------------------------- the gates

ROUTE_GATES: Final[dict[tuple[str, str], Gate]] = {
    # keys_agents
    ("GET", "/api/v1/tenants/me/keys"): "keys_agents",
    ("POST", "/api/v1/tenants/me/keys"): "keys_agents",
    ("PATCH", "/api/v1/tenants/me/keys/{key_id}"): "keys_agents",
    ("DELETE", "/api/v1/tenants/me/keys/{key_id}"): "keys_agents",
    ("POST", "/api/v1/tenants/me/keys/{key_id}/rotate"): "keys_agents",
    ("POST", "/api/v1/tenants/me/rotate-key"): "keys_agents",
    ("GET", "/api/v1/corpora/{corpus_id}/keys"): "keys_agents",
    ("POST", "/api/v1/corpora/{corpus_id}/keys"): "keys_agents",
    ("PATCH", "/api/v1/corpora/{corpus_id}/keys/{key_id}"): "keys_agents",
    ("DELETE", "/api/v1/corpora/{corpus_id}/keys/{key_id}"): "keys_agents",
    ("POST", "/api/v1/corpora/{corpus_id}/keys/{key_id}/rotate"): "keys_agents",
    ("POST", "/api/v1/corpora/{corpus_id}/rotate-key"): "keys_agents",
    # webhooks
    ("GET", "/api/v1/corpora/{corpus_id}/webhooks"): "webhooks",
    ("POST", "/api/v1/corpora/{corpus_id}/webhooks"): "webhooks",
    ("PATCH", "/api/v1/corpora/{corpus_id}/webhooks/{webhook_id}"): "webhooks",
    ("DELETE", "/api/v1/corpora/{corpus_id}/webhooks/{webhook_id}"): "webhooks",
    ("GET", "/api/v1/webhooks"): "webhooks",
    ("POST", "/api/v1/webhooks"): "webhooks",
    ("PATCH", "/api/v1/webhooks/{webhook_id}"): "webhooks",
    ("DELETE", "/api/v1/webhooks/{webhook_id}"): "webhooks",
    ("POST", "/api/v1/webhooks/{webhook_id}/test"): "webhooks",
    # quality (writes only; run results stay readable)
    ("POST", "/api/v1/corpora/{corpus_id}/eval/runs"): "quality",
    ("POST", "/api/v1/corpora/{corpus_id}/eval/criteria"): "quality",
    ("PATCH", "/api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}"): "quality",
    ("DELETE", "/api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}"): "quality",
    ("PUT", "/api/v1/corpora/{corpus_id}/eval/settings"): "quality",
    ("PATCH", "/api/v1/corpora/{corpus_id}/eval/samples/{sample_id}"): "quality",
    ("POST", "/api/v1/corpora/{corpus_id}/golden-sets"): "quality",
    ("POST", "/api/v1/corpora/{corpus_id}/golden-sets/from-template"): "quality",
    ("PATCH", "/api/v1/corpora/{corpus_id}/golden-sets/{set_id}"): "quality",
    ("DELETE", "/api/v1/corpora/{corpus_id}/golden-sets/{set_id}"): "quality",
    ("POST", "/api/v1/corpora/{corpus_id}/golden-sets/{set_id}/pairs"): "quality",
    ("POST", "/api/v1/golden"): "quality",
    ("PATCH", "/api/v1/golden/{pair_id}"): "quality",
    ("DELETE", "/api/v1/golden/{pair_id}"): "quality",
    ("POST", "/api/v1/golden/import"): "quality",
    ("POST", "/api/v1/golden/from-feedback"): "quality",
    # models
    ("POST", "/api/v1/models/discover/openrouter"): "models",
    # retrieval
    ("POST", "/api/v1/migrations/{migration_id}/approve"): "retrieval",
    ("POST", "/api/v1/migrations/{migration_id}/cancel"): "retrieval",
    ("POST", "/api/v1/migrations/{migration_id}/retry"): "retrieval",
    ("POST", "/api/v1/migrations/{migration_id}/rollback"): "retrieval",
    # widget_settings (code-only): delete and rotate; create is decided in the route (one key allowed)
    ("DELETE", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}"): "widget_settings",
    ("POST", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}/rotate"): "widget_settings",
}

# Body fields that are configuration on a route that is otherwise fine (a name change on a
# knowledge base is not gated, its `llm_config` is). Checked by the route, because the
# middleware never reads bodies.
FIELD_GATES: Final[dict[tuple[str, str], dict[str, Gate]]] = {
    ("POST", "/api/v1/corpora"): {
        "llm_config": "models",
        "prompt_config": "models",
        "retrieval_config": "retrieval",
        "embedding_config": "retrieval",
    },
    ("PUT", "/api/v1/corpora/{corpus_id}"): {
        "llm_config": "models",
        "prompt_config": "models",
        "retrieval_config": "retrieval",
        "embedding_config": "retrieval",
    },
    ("PATCH", "/api/v1/config"): {"prompt_config": "models", "retrieval_config": "retrieval"},
    ("PATCH", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}"): {
        "name": "widget_settings",
        "rate_limit_per_minute": "widget_settings",
        "daily_message_limit": "widget_settings",
    },
}


def _matchers(
    table: Mapping[tuple[str, str], Any],
) -> dict[str, list[tuple[re.Pattern[str], str, Any]]]:
    out: dict[str, list[tuple[re.Pattern[str], str, Any]]] = {}
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        entries = sorted(
            ((tpl, value) for (m, tpl), value in table.items() if m == method),
            key=lambda e: _specificity(e[0]),
        )
        out[method] = [(_pattern(tpl), tpl, value) for tpl, value in entries]
    return out


_ROUTE_MATCHERS: Final = _matchers(ROUTE_GATES)
_FIELD_MATCHERS: Final = _matchers(FIELD_GATES)


def route_gate(method: str, path: str) -> Gate | None:
    """The gate a `method path` is behind, or None when it is not a gated route."""
    method = "GET" if method.upper() == "HEAD" else method.upper()
    path = path.rstrip("/") or "/"
    for pattern, _tpl, gate in _ROUTE_MATCHERS.get(method, ()):
        if pattern.match(path):
            return gate  # type: ignore[no-any-return]
    return None


def field_gates(method: str, path: str) -> dict[str, Gate]:
    """The body-field gates of `method path` (empty when it has none)."""
    method = method.upper()
    path = path.rstrip("/") or "/"
    for pattern, _tpl, fields in _FIELD_MATCHERS.get(method, ()):
        if pattern.match(path):
            return dict(fields)
    return {}


# ------------------------------------------------------------------- refusing


LLM_OVERRIDE_HEADERS: Final = ("x-llm-provider", "x-llm-model", "x-llm-api-key", "x-llm-base-url")


def not_in_plan_message(support: str | None) -> str:
    who = support or "the team that set up your account"
    return f"This isn't included in your plan. Ask {who} if you need it."


def refusal_response(gate: Gate, support: str | None = None) -> "JSONResponse":
    """The 403 for the middleware, which cannot raise into FastAPI's handlers."""
    return error_response(
        403,
        not_in_plan_message(support),
        "feature_not_in_plan",
        feature=FEATURE_OF_GATE[gate],
    )


async def support_contact(request: "Request") -> str | None:
    """The deployer's support e-mail from branding. Best-effort; None when unset."""
    try:
        from callobuzz_rag.accounts.settings_store import get_engine_settings

        factory = getattr(request.app.state, "session_factory", None)
        if factory is not None:
            email = (await get_engine_settings(factory)).branding.support_email
            return str(email) if email else None
    except Exception:
        logger.warning("Could not read branding for the not-in-plan message", exc_info=True)
    return None


async def gate_session(
    request: "Request", tenant: Any, raw_features: Mapping[str, Any] | None
) -> "JSONResponse | None":
    """Apply a business's plan to one session request (the auth middleware calls this).

    Sets `request.state.hidden_gates`; returns the 403 when the route is behind a hidden
    gate. The operator workspace is never gated. One call, no query: the flags came with
    the session lookup.
    """
    if getattr(tenant, "is_operator", False):
        request.state.hidden_gates = frozenset()
        request.state.dashboard_features = None
        return None
    hidden = hidden_gates(raw_features)
    request.state.hidden_gates = hidden
    request.state.dashboard_features = parse_features(raw_features)
    if not hidden:
        return None
    if "models" in hidden and any(h in request.headers for h in LLM_OVERRIDE_HEADERS):
        # The answer model is chosen per request too (the playground's override); the data
        # routes are never gated, so the headers are what the plan has to refuse.
        return refusal_response("models", await support_contact(request))
    gate = route_gate(request.method, request.url.path)
    if gate is None or gate not in hidden:
        return None
    return refusal_response(gate, await support_contact(request))


def hidden_on(request: "Request") -> frozenset[Gate]:
    """The gates hidden for this request's principal (empty unless a gated session)."""
    return getattr(request.state, "hidden_gates", None) or frozenset()


async def refuse_hidden(request: "Request", gate: Gate) -> None:
    """Raise `feature_not_in_plan` (403) when `gate` is hidden for the caller."""
    if gate in hidden_on(request):
        raise APIError(
            403,
            not_in_plan_message(await support_contact(request)),
            "feature_not_in_plan",
            feature=FEATURE_OF_GATE[gate],
        )


async def refuse_hidden_fields(request: "Request", body: BaseModel | Mapping[str, Any]) -> None:
    """Raise when the request body sets a field behind a hidden gate.

    A field counts as set when the client sent it (`model_fields_set`), so a PUT that only
    renames the knowledge base passes while one carrying `llm_config` is refused.
    """
    # L5a demo
    from callobuzz_rag.demo import guard as demo_guard

    settings = getattr(request.app.state, "settings", None)
    if settings is not None and getattr(settings, "demo_mode", False) is True:
        demo_guard.raise_for_fields(
            demo_mode=True,
            method=request.method,
            template=request.url.path,
            fields_set=body.model_fields_set if isinstance(body, BaseModel) else set(body),
            admin_key=demo_guard.is_admin_key(
                request.headers.get("x-api-key"), settings.admin_api_key.get_secret_value()
            ),
        )
    # /L5a demo
    hidden = hidden_on(request)
    if not hidden:
        return
    gates = field_gates(request.method, request.url.path)
    if not gates:
        return
    sent = body.model_fields_set if isinstance(body, BaseModel) else set(body)
    for name, gate in gates.items():
        if name in sent and gate in hidden:
            await refuse_hidden(request, gate)
