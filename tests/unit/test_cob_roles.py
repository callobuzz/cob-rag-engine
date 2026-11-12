"""Who is asking COB: the role from the middleware's request state, keys refused."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.cob.orchestrator import caller_from_request
from callobuzz_rag.cob.types import CobRole, allowed_audiences

SUPER = SimpleNamespace(id=uuid.uuid4(), is_superadmin=True)
ALICE = SimpleNamespace(id=uuid.uuid4(), is_superadmin=False)
RAVI = SimpleNamespace(id=uuid.uuid4(), is_superadmin=False)
OPERATOR_WS = SimpleNamespace(id=uuid.uuid4(), is_operator=True)
SHREE = SimpleNamespace(id=uuid.uuid4(), is_operator=False)


def req(**state: Any) -> Any:
    state.setdefault("session_id", uuid.uuid4())
    return SimpleNamespace(
        state=SimpleNamespace(**state), headers={"authorization": "Bearer abc.def"}
    )


@pytest.mark.parametrize(
    ("state", "role"),
    [
        (dict(is_admin=True, user=SUPER, tenant=None, role=None), CobRole.ADMIN),
        (
            dict(is_admin=False, user=SUPER, tenant=OPERATOR_WS, role="owner"),
            CobRole.OPERATOR_OWNER,
        ),
        (dict(is_admin=False, user=ALICE, tenant=SHREE, role="owner"), CobRole.OWNER),
        (dict(is_admin=False, user=RAVI, tenant=SHREE, role="member"), CobRole.MEMBER),
    ],
)
def test_caller_role(state: dict[str, Any], role: CobRole) -> None:
    caller = caller_from_request(req(auth_kind="session", **state))
    assert caller.role == role
    assert caller.user_id == state["user"].id
    assert caller.authorization == "Bearer abc.def"
    expected_tenant = None if role == CobRole.ADMIN else state["tenant"].id
    assert caller.tenant_id == expected_tenant


@pytest.mark.parametrize("kind", ["api_key"])
def test_keys_are_refused(kind: str) -> None:
    with pytest.raises(APIError) as e:
        caller_from_request(req(auth_kind=kind, is_admin=True, user=None))
    assert e.value.status_code == 403 and e.value.code == "session_required"


def test_no_credential_is_401() -> None:
    with pytest.raises(APIError) as e:
        caller_from_request(req())
    assert e.value.status_code == 401


def test_member_and_owner_never_get_admin_audience() -> None:
    for role in (CobRole.OWNER, CobRole.MEMBER, CobRole.PUBLIC):
        assert "admin" not in allowed_audiences(role)
