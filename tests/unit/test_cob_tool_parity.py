"""COB's read tools against the engine's route table: no tool reaches past what a role may see.

Every route a tool declares is classified (`ROUTE_OPS`, or a public route), read tools only GET,
platform routes only for the operator, and a route that needs full (manage) permission is never
offered to a member. Tools read through the real routes, so these tests keep the declared
routes and the declared roles from drifting apart.
"""

import re
from pathlib import Path

from callobuzz_rag.api.permissions import PUBLIC_ROUTES, ROUTE_OPS, _pattern
from callobuzz_rag.cob import tools as _tools
from callobuzz_rag.cob.tools.registry import REGISTRY, tools_for
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

READ_DIR = Path(_tools.__file__).parent / "read"


def test_every_tool_route_is_classified() -> None:
    for tool in REGISTRY.values():
        for rc in tool.routes:
            key = (rc.method, rc.path)
            assert key in ROUTE_OPS or key in PUBLIC_ROUTES, (tool.name, rc)


def test_read_tools_only_get() -> None:
    for t in REGISTRY.values():
        if t.kind == "read":
            assert {rc.method for rc in t.routes} == {"GET"}, t.name


def test_admin_routes_only_for_the_admin_role() -> None:
    for t in REGISTRY.values():
        if any(rc.path.startswith("/api/v1/admin/") for rc in t.routes):
            assert t.roles == frozenset({CobRole.ADMIN}), t.name


def test_members_never_get_manage_tools() -> None:
    for t in REGISTRY.values():
        if any(ROUTE_OPS.get((rc.method, rc.path)) == "manage" for rc in t.routes):
            assert CobRole.MEMBER not in t.roles, t.name


def test_the_workspace_routes_are_not_for_the_operator_view() -> None:
    """`/tenants/me/...` belongs to a workspace session; the operator view has no workspace."""
    for t in REGISTRY.values():
        if any(rc.path.startswith("/api/v1/tenants/me") for rc in t.routes):
            assert CobRole.ADMIN not in t.roles, t.name


def test_the_public_cob_has_no_tools() -> None:
    assert tools_for(CobRole.PUBLIC) == []
    for t in REGISTRY.values():
        assert CobRole.PUBLIC not in t.roles


def test_owner_only_tools_are_the_ones_behind_manage_routes() -> None:
    owner_only = {t.name for t in REGISTRY.values() if t.kind == "read" and t.owner_only}
    manage = {
        t.name
        for t in REGISTRY.values()
        if t.kind == "read"  # COB5: the action tools are checked in test_cob_action_parity
        and any(ROUTE_OPS.get((rc.method, rc.path)) == "manage" for rc in t.routes)
        and not any(rc.path.startswith("/api/v1/admin/") for rc in t.routes)
    }
    assert owner_only == manage == {"api_keys"}


def test_every_tool_is_described_for_the_router() -> None:
    for t in REGISTRY.values():
        assert t.description.endswith("."), t.name
        assert issubclass(t.args_model, StrictRequest), t.name
        assert t.name and t.name == t.name.lower() and " " not in t.name


def test_the_operator_in_a_workspace_gets_the_workspace_tools_only() -> None:
    names = {t.name for t in tools_for(CobRole.OPERATOR_OWNER)}
    assert {"plan_usage", "api_keys", "my_costs"} <= names
    assert not names & {"engine_health", "customers", "platform_costs"}


def test_every_route_template_matches_its_own_sample_path() -> None:
    for t in REGISTRY.values():
        for rc in t.routes:
            sample = re.sub(r"\{[^}]+\}", "9f0b6c1a", rc.path)
            assert _pattern(rc.path).match(sample), (t.name, rc.path)


def test_card_code_never_reads_a_secret_or_a_contact_field() -> None:
    """The read tools never even look at these fields, so a card cannot leak them."""
    forbidden = (
        "key_hash",
        "key_prefix",
        "public_key",
        "embed_snippet",
        "api_key_encrypted",
        "owner_emails",
        '"phone"',
        '"contact"',
        '"email"',
        "password",
    )
    for path in READ_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for word in forbidden:
            assert word not in source, (path.name, word)
