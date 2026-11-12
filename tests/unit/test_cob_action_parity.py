"""COB's action tools against the engine's route table: an action never offers more than the
route it calls would allow the person (COB5).

The tools call the real routes, so these tests keep the declared routes, the declared roles and
the destructive / owner-only flags from drifting apart from `ROUTE_OPS` and `OWNER_ONLY`."""

import json
import re
from pathlib import Path

from callobuzz_rag.api.permissions import ROUTE_OPS, _pattern
from callobuzz_rag.cob import tools as _tools
from callobuzz_rag.cob.tools.actions.common import ActionBase
from callobuzz_rag.cob.tools.base import RouteCall
from callobuzz_rag.cob.tools.cards import DATA_PATH
from callobuzz_rag.cob.tools.registry import actions, tools_for
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest
from tests.unit import scope_harness

ACTION_DIR = Path(_tools.__file__).parent / "actions"
TYPED = {("POST", "/api/v1/admin/tenants/{tenant_id}/suspend")}

EXPECTED = {
    "create_knowledge_base", "create_widget_key", "add_allowed_site", "invite_member",
    "change_member_role", "rotate_key", "toggle_assistant", "publish_assistant",
    "switch_reply_language", "retry_ingestion", "start_guided_setup", "approve_customer",
    "change_customer_plan", "delete_knowledge_base", "delete_document", "delete_key",
    "suspend_customer", "delete_customer",
}  # fmt: skip


def owner_only_templates() -> set[tuple[str, str]]:
    """`OWNER_ONLY` (sample paths) mapped to the `ROUTE_OPS` templates they exercise."""
    found: set[tuple[str, str]] = set()
    for method, sample, _body, _portal in scope_harness.OWNER_ONLY:
        if method == "PUT":  # depends on the body: not a whole route
            continue
        concrete = re.sub(r"\{[^}]+\}", "x", sample.split("?")[0])
        for m, template in ROUTE_OPS:
            if m == method and _pattern(template).match(concrete):
                found.add((m, template))
    return found


OWNER_ONLY_TEMPLATES = owner_only_templates()


def _writes(tool: ActionBase) -> list[RouteCall]:
    return [rc for rc in tool.routes if rc.method != "GET"]


def test_there_are_exactly_the_eighteen_pinned_tools() -> None:
    assert {t.name for t in actions()} == EXPECTED


def test_action_routes_are_real_and_not_read() -> None:
    for t in actions():
        for rc in t.routes:
            assert (rc.method, rc.path) in ROUTE_OPS, (t.name, rc)
        for rc in _writes(t):
            assert ROUTE_OPS[(rc.method, rc.path)] in {"write", "delete", "manage"}, (t.name, rc)


def test_destructive_matches_the_routes() -> None:
    for t in actions():
        is_destructive = any(
            rc.method == "DELETE" or (rc.method, rc.path) in TYPED for rc in _writes(t)
        )
        assert t.destructive == is_destructive, t.name


def test_owner_only_tools_exclude_members() -> None:
    for t in actions():
        owner_only = any((rc.method, rc.path) in OWNER_ONLY_TEMPLATES for rc in _writes(t))
        assert t.owner_only == owner_only, t.name
        manage_in_workspace = any(
            ROUTE_OPS[(rc.method, rc.path)] == "manage" and not rc.path.startswith("/api/v1/admin/")
            for rc in _writes(t)
        )
        if owner_only or manage_in_workspace:
            assert CobRole.MEMBER not in t.roles, t.name


def test_a_route_members_may_use_keeps_the_member_role() -> None:
    """The reverse: a tool whose routes a member may call is offered to members, so COB is no
    stricter (or looser) than the dashboard."""
    members = {t.name for t in actions() if CobRole.MEMBER in t.roles}
    assert members == {"retry_ingestion", "start_guided_setup"}


def test_admin_tools_only_admin() -> None:
    for t in actions():
        if any(rc.path.startswith("/api/v1/admin/") for rc in t.routes):
            assert t.roles == frozenset({CobRole.ADMIN}), t.name
    admin = {t.name for t in actions() if t.roles == frozenset({CobRole.ADMIN})}
    assert admin == {
        "approve_customer",
        "change_customer_plan",
        "suspend_customer",
        "delete_customer",
    }


def test_the_workspace_routes_are_not_for_the_operator_view() -> None:
    for t in actions():
        if any(rc.path.startswith("/api/v1/tenants/me") for rc in t.routes):
            assert CobRole.ADMIN not in t.roles, t.name


def test_the_public_cob_and_keys_have_no_actions() -> None:
    assert not [t for t in tools_for(CobRole.PUBLIC) if t.kind == "action"]
    for t in actions():
        assert CobRole.PUBLIC not in t.roles


def test_risk_follows_the_kind_of_change() -> None:
    for t in actions():
        assert isinstance(t, ActionBase)
        assert (t.risk == "high") == t.destructive, t.name
    medium = {t.name for t in actions() if t.risk == "medium"}
    assert medium == {
        "change_member_role", "rotate_key", "toggle_assistant", "publish_assistant",
        "switch_reply_language", "approve_customer", "change_customer_plan",
    }  # fmt: skip


def test_destructive_tools_say_how_to_read_the_name() -> None:
    for t in actions():
        assert isinstance(t, ActionBase)
        overrides = type(t).current_name is not ActionBase.current_name
        assert overrides == t.destructive, t.name


def test_models_are_strict_and_tools_are_described() -> None:
    for t in actions():
        assert isinstance(t, ActionBase)
        assert issubclass(t.args_model, StrictRequest) and issubclass(t.exec_model, StrictRequest)
        assert t.description.endswith("."), t.name


def test_only_the_link_tool_has_no_write_route() -> None:
    assert {t.name for t in actions() if not _writes(t)} == {"start_guided_setup"}


def test_action_code_never_reads_a_contact_field_or_a_hash() -> None:
    forbidden = ("key_hash", "api_key_encrypted", "password", '"phone"', '"contact"')
    for path in ACTION_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for word in forbidden:
            assert word not in source, (path.name, word)


def test_every_string_an_action_uses_exists_in_three_languages() -> None:
    strings = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    used: set[str] = set()
    for path in [*ACTION_DIR.glob("*.py"), ACTION_DIR.parent.parent / "proposals.py"]:
        source = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'\bts\(\s*"([a-z_]+)"', source))
        used |= set(re.findall(r'\bask\(\s*ctx,\s*"([a-z_]+)"', source))
        used |= set(re.findall(r'"((?:sum|title|done|link)_[a-z_]+)"', source))
    for tool in EXPECTED:
        used.add(f"link_{tool}")
        if tool not in ("toggle_assistant", "start_guided_setup"):
            used |= {f"title_{tool}", f"sum_{tool}"}
    for suffix in ("on", "off"):
        used |= {f"{kind}_toggle_assistant_{suffix}" for kind in ("title", "sum", "done")}
    used |= {"lang_en", "lang_hi", "lang_hi-Latn", "role_owner", "role_member"}
    missing = sorted(k for k in used if k not in strings)
    assert not missing, missing
    for key in used:
        for lang in ("en", "hi", "hi-Latn"):
            assert strings[key].get(lang), (key, lang)
