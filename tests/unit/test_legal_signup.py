"""P3 T5: sign-up records which Terms and Privacy versions a person accepted, only when both are
published; and the operator can read who accepted what."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.db.models.email_code import EmailCode
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.legal import LegalAcceptance, LegalDocument
from callobuzz_rag.db.models.tenant import Tenant
from tests.unit.test_auth_routes import ADMIN, World, auth_app, req
from tests.unit.test_signup import code_for, signup, user

WHEN = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


@pytest.fixture
def world() -> World:
    w = World()
    w.db.add(EngineSettingsRow(id=1, value={"signup_mode": "open"}))
    invalidate_engine_settings_cache()
    return w


def publish(world: World, slug: str, version: int) -> LegalDocument:
    return world.db.add(
        LegalDocument(
            slug=slug,
            version=version,
            title=slug.title(),
            body_md="x",
            body_filled="x",
            body_html="<p>x</p>",
            revision=1,
            published_at=WHEN,
            created_at=WHEN,
            updated_at=WHEN,
        )
    )


async def verify(app: Any, world: World, email: str = "asha@shree.example") -> Any:
    return await req(
        app, "POST", "/api/v1/auth/verify", json={"email": email, "code": code_for(world, email)}
    )


async def test_nothing_published_signs_up_exactly_as_today(world: World) -> None:
    with auth_app(world) as app:
        res = await signup(app)
        assert res.status_code == 202, res.text
        assert (await verify(app, world)).status_code == 200
    assert world.db.all(LegalAcceptance) == []


async def test_only_one_of_the_two_published_asks_for_nothing(world: World) -> None:
    publish(world, "terms", 1)
    with auth_app(world) as app:
        res = await signup(app)
    assert res.status_code == 202, res.text  # both must be published before sign-up asks


async def test_acceptance_is_required_when_both_are_published(world: World) -> None:
    publish(world, "terms", 1)
    publish(world, "privacy", 1)
    with auth_app(world) as app:
        refused = await signup(app)
        partial = await signup(app, accept_legal={"terms": 1})
        accepted = await signup(app, accept_legal={"terms": 1, "privacy": 1})
    assert refused.status_code == 422
    assert refused.json()["code"] == "legal_acceptance_required"
    assert partial.status_code == 422
    assert accepted.status_code == 202, accepted.text
    code = next(c for c in world.db.all(EmailCode) if c.purpose == "verify")
    assert code.payload["legal"] == {"terms": 1, "privacy": 1}


async def test_verify_writes_one_row_per_page(world: World) -> None:
    terms, privacy = publish(world, "terms", 1), publish(world, "privacy", 1)
    with auth_app(world) as app:
        await signup(app, accept_legal={"terms": 1, "privacy": 1})
        assert (await verify(app, world)).status_code == 200
    rows = world.db.all(LegalAcceptance)
    assert sorted((r.slug, r.version, r.via) for r in rows) == [
        ("privacy", 1, "signup"),
        ("terms", 1, "signup"),
    ]
    tenant = next(t for t in world.db.all(Tenant) if t.name == "Kaveri Foods")
    asha = user(world, "asha@shree.example")
    assert asha is not None
    assert {r.document_id for r in rows} == {terms.id, privacy.id}
    for r in rows:
        assert r.tenant_id == tenant.id and r.user_id == asha.id
        assert r.email == "asha@shree.example"


async def test_a_newer_version_after_signup_still_records_what_they_accepted(
    world: World,
) -> None:
    v1 = publish(world, "terms", 1)
    publish(world, "privacy", 1)
    with auth_app(world) as app:
        await signup(app, accept_legal={"terms": 1, "privacy": 1})
        publish(world, "terms", 2)
        assert (await verify(app, world)).status_code == 200
    terms = next(r for r in world.db.all(LegalAcceptance) if r.slug == "terms")
    assert terms.version == 1 and terms.document_id == v1.id


async def test_an_older_version_is_a_409(world: World) -> None:
    publish(world, "terms", 1)
    publish(world, "terms", 2)
    publish(world, "privacy", 1)
    with auth_app(world) as app:
        res = await signup(app, accept_legal={"terms": 1, "privacy": 1})
    assert res.status_code == 409
    assert res.json()["code"] == "legal_version_changed"
    assert res.json()["required"] == {"terms": 2, "privacy": 1}


async def test_unknown_fields_in_accept_legal_are_refused(world: World) -> None:
    with auth_app(world) as app:
        res = await signup(app, accept_legal={"terms": 1, "cookies": 1})
    assert res.status_code == 422


async def test_signing_up_twice_keeps_the_latest_payload_and_one_set_of_rows(
    world: World,
) -> None:
    publish(world, "terms", 1)
    publish(world, "privacy", 1)
    with auth_app(world) as app:
        await signup(app, accept_legal={"terms": 1, "privacy": 1})
        publish(world, "terms", 2)
        second = await signup(app, accept_legal={"terms": 2, "privacy": 1})
        assert second.status_code == 202, second.text
        assert (await verify(app, world)).status_code == 200
    rows = world.db.all(LegalAcceptance)
    assert sorted((r.slug, r.version) for r in rows) == [("privacy", 1), ("terms", 2)]


async def test_acceptance_outlives_a_deleted_user(world: World) -> None:
    publish(world, "terms", 1)
    publish(world, "privacy", 1)
    with auth_app(world) as app:
        await signup(app, accept_legal={"terms": 1, "privacy": 1})
        await verify(app, world)
    asha = user(world, "asha@shree.example")
    assert asha is not None
    # what SET NULL does in PostgreSQL (the integration test covers the real thing)
    for row in world.db.all(LegalAcceptance):
        row.user_id = None
    world.db._remove(asha)
    assert {r.email for r in world.db.all(LegalAcceptance)} == {"asha@shree.example"}
    assert all(r.user_id is None for r in world.db.all(LegalAcceptance))


async def test_operator_reads_acceptances_as_json_and_csv(world: World) -> None:
    publish(world, "terms", 1)
    publish(world, "privacy", 1)
    with auth_app(world) as app:
        await signup(app, accept_legal={"terms": 1, "privacy": 1})
        await verify(app, world)
        res = await req(app, "GET", "/api/v1/admin/legal/acceptances", key=ADMIN)
        csv = await req(app, "GET", "/api/v1/admin/legal/acceptances?format=csv", key=ADMIN)
        none = await req(
            app,
            "GET",
            "/api/v1/admin/legal/acceptances?tenant_id=00000000-0000-0000-0000-000000000001",
            key=ADMIN,
        )
        anon = await req(app, "GET", "/api/v1/admin/legal/acceptances")
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["total"] == 2
    assert {(i["page"], i["version"]) for i in body["items"]} == {("terms", 1), ("privacy", 1)}
    assert all(i["workspace"] == "Kaveri Foods" for i in body["items"])
    assert csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().splitlines()
    assert lines[0] == "accepted_at,email,workspace,page,version"
    assert len(lines) == 3 and "asha@shree.example" in lines[1]
    assert none.json() == {"items": [], "total": 0}
    assert anon.status_code == 401
