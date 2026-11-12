"""LP1 T4: no workspace credential reaches the website editor; only the operator does."""

import uuid
from typing import Any

import pytest

from tests.unit.scope_harness import call, scoped_app

ADMIN_ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/api/v1/admin/landing", None),
    ("PUT", "/api/v1/admin/landing/draft", {"base_revision": 1, "content": {}}),
    ("PUT", "/api/v1/admin/landing/settings", {"enabled": True}),
    ("POST", "/api/v1/admin/landing/theme/preview", {"brand_color": "#0f766e"}),
    ("POST", "/api/v1/admin/landing/publish", {"base_revision": 1}),
    ("GET", "/api/v1/admin/landing/versions", None),
    ("POST", "/api/v1/admin/landing/versions/1/restore", {"base_revision": 1}),
    ("POST", "/api/v1/admin/landing/preview-link", {"page": "home", "lang": "en"}),
    ("POST", "/api/v1/admin/landing/assets", None),
    ("DELETE", "/api/v1/admin/landing/assets/{asset_id}", None),
    # LP1 landing: more rows are added by the tasks that add the routes
    # /LP1 landing
    # LP2 landing
    ("GET", "/api/v1/admin/landing/presets", None),
    ("GET", "/api/v1/admin/landing/presets/clinic", None),
    (
        "POST",
        "/api/v1/admin/landing/generate",
        {"corpus_id": "00000000-0000-0000-0000-000000000001", "language": "en"},
    ),
    # /LP2 landing
    # LP4 landing
    (
        "POST",
        "/api/v1/admin/landing/assistant",
        {"corpus_id": "00000000-0000-0000-0000-000000000001"},
    ),
    ("DELETE", "/api/v1/admin/landing/assistant", None),
    # /LP4 landing
]


@pytest.mark.parametrize("key", ["tk_a", "tk_b", "ck_a", "ck_b", "owner_a", "member_a"])
@pytest.mark.parametrize(("method", "template", "body"), ADMIN_ROUTES)
async def test_no_workspace_credential_reaches_the_landing_editor(
    key: str, method: str, template: str, body: Any
) -> None:
    with scoped_app() as (app, _):
        res = await call(app, key, method, template.format(asset_id=uuid.uuid4()), json=body)
    assert res.status_code == 403, (key, template, res.text)


async def test_no_credential_is_401() -> None:
    with scoped_app() as (app, _):
        res = await call(app, None, "GET", "/api/v1/admin/landing")
    assert res.status_code == 401
