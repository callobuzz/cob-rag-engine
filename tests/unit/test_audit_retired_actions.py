"""Old `license.*` audit rows stay listed and findable after the actions left the catalogue."""

import pytest

from callobuzz_rag.accounts.audit import AUDIT_ACTIONS, RETIRED_AUDIT_ACTIONS
from callobuzz_rag.api.routes.audit import _action_filter
from tests.unit.test_audit_api import _Capture, _get, _row

RETIRED = {
    "license.activated",
    "license.activation_failed",
    "license.deactivated",
    "license.state_changed",
}


def test_the_licence_actions_are_retired() -> None:
    assert RETIRED == RETIRED_AUDIT_ACTIONS
    assert not (RETIRED & set(AUDIT_ACTIONS))


@pytest.mark.parametrize("action", [*sorted(RETIRED), "license."])
def test_the_filter_accepts_a_retired_action_and_its_prefix(action: str) -> None:
    _action_filter(action)  # no HTTPException


async def test_record_audit_refuses_a_retired_action() -> None:
    from callobuzz_rag.accounts.audit import record_audit

    with pytest.raises(AssertionError, match="unknown audit action"):
        await record_audit(None, None, "license.activated")  # type: ignore[arg-type]


async def test_an_old_row_is_listed_and_found_by_its_action_and_prefix() -> None:
    row = _row(action="license.activated", target_type="license")
    listed = await _get("admin", _Capture([row]))
    assert listed.status_code == 200, listed.text
    assert [i["action"] for i in listed.json()["items"]] == ["license.activated"]
    for action in ("license.", "license.state_changed"):
        capture = _Capture([])
        res = await _get("admin", capture, action=action)
        assert res.status_code == 200, res.text
        assert res.json()["items"] == []
        assert any("license." in sql for sql in capture.sql)
