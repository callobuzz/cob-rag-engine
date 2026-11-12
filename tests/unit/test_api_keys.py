"""Named API keys: many per knowledge base and per customer, each with a permission.

The real app and middleware on the in-memory database of `accounts_fakes.py`. A key is a row
of `api_keys`; the plaintext exists once, in the response that issued it. The route-level
permission table has its own tests in `test_key_permissions.py`.
"""

import uuid
from typing import Any

import pytest

from callobuzz_rag.core.secrets import hash_api_key, verify_api_key
from callobuzz_rag.db.models.api_key import ApiKey
from callobuzz_rag.db.models.corpus import Corpus
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req

TK_DEFAULT_SECRET = "legacysecretoftheoldsinglekey"


class Setup:
    """Customer Shree Labs with a knowledge base, each holding a migrated default key."""

    def __init__(self) -> None:
        self.world = World()
        self.tenant = self.world.shree
        self.corpus = self.world.db.add(
            Corpus(
                name="Support FAQ",
                tenant_id=self.tenant.id,
                qdrant_collection="corpus_abcd1234",
                api_key_hash="",
            )
        )
        self.tk = self.add_key(self.tenant, "tk", f"tk_{str(self.tenant.id)[:8]}_legacy")
        self.ck = self.add_key(self.corpus, "ck", "ck_abcd1234_legacy")

    def add_key(
        self,
        owner: Any,
        kind: str,
        plaintext: str,
        *,
        permission: str = "full",
        name: str = "Default key",
        lookup: str | None = None,
        is_default: bool = True,
    ) -> str:
        self.world.db.add(
            ApiKey(
                corpus_id=owner.id if kind == "ck" else None,
                tenant_id=owner.id if kind == "tk" else None,
                id_short=plaintext.split("_")[1],
                lookup=lookup,
                name=name,
                key_prefix=f"{kind}_...",
                key_hash=hash_api_key(plaintext),
                permission=permission,
                is_default=is_default,
                created_by="migrated",
            )
        )
        return plaintext

    def keys_of(self, owner: Any) -> list[ApiKey]:
        return [k for k in self.world.db.all(ApiKey) if owner.id in (k.corpus_id, k.tenant_id)]


@pytest.fixture
def s() -> Setup:
    return Setup()


def tenant_keys(path: str = "") -> str:
    return f"/api/v1/tenants/me/keys{path}"


def corpus_keys(s: Setup, path: str = "") -> str:
    return f"/api/v1/corpora/{s.corpus.id}/keys{path}"


# ------------------------------------------------------------------ creating


async def test_create_returns_the_key_once_and_lists_only_its_prefix(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = await req(
            app,
            "POST",
            tenant_keys(),
            key=s.tk,
            json={"name": "Agent - Cursor", "permission": "read"},
        )
        listed = await req(app, "GET", tenant_keys(), key=s.tk)
    assert made.status_code == 201, made.text
    body = made.json()
    key = body["api_key"]
    short = str(s.tenant.id)[:8]
    assert key.startswith(f"tk_{short}_") and len(key) > 30
    assert body["name"] == "Agent - Cursor" and body["permission"] == "read"
    assert body["is_default"] is False and body["created_by"] == "key: Default key"
    assert body["last_used_at"] is None
    assert key.startswith(body["key_prefix"].removesuffix("..."))
    assert body["key_prefix"] != key

    rows = {k["name"]: k for k in listed.json()["keys"]}
    assert set(rows) == {"Default key", "Agent - Cursor"}
    assert key not in listed.text and "hash" not in listed.text
    assert "api_key" not in rows["Agent - Cursor"]
    assert rows["Default key"]["permission"] == "full"

    stored = next(k for k in s.keys_of(s.tenant) if k.name == "Agent - Cursor")
    assert verify_api_key(key, stored.key_hash)
    assert stored.lookup == key.split("_", 2)[2][:8]
    assert key not in repr(stored.key_hash)


async def test_permission_defaults_to_read_and_must_be_known(s: Setup) -> None:
    with auth_app(s.world) as app:
        default = await req(app, "POST", tenant_keys(), key=s.tk, json={"name": "A"})
        bad = await req(
            app, "POST", tenant_keys(), key=s.tk, json={"name": "B", "permission": "admin"}
        )
        unknown = await req(
            app, "POST", tenant_keys(), key=s.tk, json={"name": "C", "scopes": ["x"]}
        )
        blank = await req(app, "POST", tenant_keys(), key=s.tk, json={"name": ""})
    assert default.json()["permission"] == "read"
    assert bad.status_code == 422 and unknown.status_code == 422 and blank.status_code == 422


async def test_a_new_key_works_with_its_permission(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = await req(
            app, "POST", tenant_keys(), key=s.tk, json={"name": "Reader", "permission": "read"}
        )
        new = made.json()["api_key"]
        who = await req(app, "GET", "/api/v1/whoami", key=new)
        refused = await req(app, "POST", tenant_keys(), key=new, json={"name": "Escalate"})
        listing = await req(app, "GET", tenant_keys(), key=new)
    assert who.status_code == 200 and who.json()["kind"] == "tenant"
    for res in (refused, listing):
        assert res.status_code == 403, res.text
        assert res.json()["code"] == "key_permission_denied"
        assert res.json()["required"] == "full" and res.json()["permission"] == "read"
    assert len(s.keys_of(s.tenant)) == 2  # the refused create made nothing


async def test_migrated_default_key_keeps_working_unchanged(s: Setup) -> None:
    """Same hash, no lookup segment, full permission: nothing was re-issued."""
    with auth_app(s.world) as app:
        tk = await req(app, "GET", tenant_keys(), key=s.tk)
        ck = await req(app, "GET", corpus_keys(s), key=s.ck)
        wrong = await req(app, "GET", "/api/v1/whoami", key=s.tk + "x")
    assert tk.status_code == 200 and ck.status_code == 200
    assert wrong.status_code == 401 and wrong.json()["code"] == "invalid_api_key"


async def test_two_keys_of_one_owner_are_told_apart_by_their_lookup(s: Setup) -> None:
    with auth_app(s.world) as app:
        first = (await req(app, "POST", tenant_keys(), key=s.tk, json={"name": "One"})).json()
        second = (await req(app, "POST", tenant_keys(), key=s.tk, json={"name": "Two"})).json()
        a = await req(app, "GET", "/api/v1/whoami", key=first["api_key"])
        b = await req(app, "GET", "/api/v1/whoami", key=second["api_key"])
        mixed = await req(
            app,
            "GET",
            "/api/v1/whoami",
            key=first["api_key"][:-3] + "xyz",
        )
    assert a.status_code == 200 and b.status_code == 200
    assert mixed.status_code == 401


async def test_key_limit_per_owner(s: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.accounts import api_keys

    monkeypatch.setattr(api_keys, "MAX_KEYS_PER_OWNER", 2)
    with auth_app(s.world) as app:
        ok = await req(app, "POST", tenant_keys(), key=s.tk, json={"name": "Second"})
        over = await req(app, "POST", tenant_keys(), key=s.tk, json={"name": "Third"})
    assert ok.status_code == 201
    assert over.status_code == 409 and over.json()["code"] == "key_limit_reached"


# ------------------------------------------------------------------ rotate, update, revoke


async def test_rotate_replaces_only_that_key(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = (
            await req(
                app, "POST", tenant_keys(), key=s.tk, json={"name": "n8n", "permission": "write"}
            )
        ).json()
        old = made["api_key"]
        rotated = await req(app, "POST", tenant_keys(f"/{made['id']}/rotate"), key=s.tk)
        new = rotated.json()["api_key"]
        old_try = await req(app, "GET", "/api/v1/whoami", key=old)
        new_try = await req(app, "GET", "/api/v1/whoami", key=new)
        default_try = await req(app, "GET", "/api/v1/whoami", key=s.tk)
        listed = (await req(app, "GET", tenant_keys(), key=s.tk)).json()["keys"]
    assert rotated.status_code == 200 and new != old and new.startswith("tk_")
    assert rotated.json()["id"] == made["id"] and "hash" not in rotated.text
    assert old_try.status_code == 401
    assert new_try.status_code == 200 and default_try.status_code == 200
    row = next(k for k in listed if k["id"] == made["id"])
    assert row["name"] == "n8n" and row["permission"] == "write"
    assert row["key_prefix"] == rotated.json()["key_prefix"]


async def test_rotate_clears_last_used(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = (await req(app, "POST", tenant_keys(), key=s.tk, json={"name": "Bot"})).json()
        await req(app, "GET", "/api/v1/whoami", key=made["api_key"])
        used = next(
            k
            for k in (await req(app, "GET", tenant_keys(), key=s.tk)).json()["keys"]
            if k["id"] == made["id"]
        )
        await req(app, "POST", tenant_keys(f"/{made['id']}/rotate"), key=s.tk)
        after = next(
            k
            for k in (await req(app, "GET", tenant_keys(), key=s.tk)).json()["keys"]
            if k["id"] == made["id"]
        )
    assert used["last_used_at"] is not None
    assert after["last_used_at"] is None


async def test_revoke_ends_the_key_at_once_and_nothing_else(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = (await req(app, "POST", tenant_keys(), key=s.tk, json={"name": "Temp"})).json()
        works = await req(app, "GET", "/api/v1/whoami", key=made["api_key"])
        gone = await req(app, "DELETE", tenant_keys(f"/{made['id']}"), key=s.tk)
        after = await req(app, "GET", "/api/v1/whoami", key=made["api_key"])
        again = await req(app, "DELETE", tenant_keys(f"/{made['id']}"), key=s.tk)
        default_try = await req(app, "GET", "/api/v1/whoami", key=s.tk)
    assert works.status_code == 200
    assert gone.status_code == 204
    assert after.status_code == 401
    assert again.status_code == 404 and again.json()["code"] == "key_not_found"
    assert default_try.status_code == 200
    assert [k.name for k in s.keys_of(s.tenant)] == ["Default key"]


async def test_permission_change_applies_on_the_next_request(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = (
            await req(
                app, "POST", tenant_keys(), key=s.tk, json={"name": "Agent", "permission": "read"}
            )
        ).json()
        agent = made["api_key"]
        before = await req(app, "GET", tenant_keys(), key=agent)
        patched = await req(
            app, "PATCH", tenant_keys(f"/{made['id']}"), key=s.tk, json={"permission": "full"}
        )
        after = await req(app, "GET", tenant_keys(), key=agent)
        renamed = await req(
            app, "PATCH", tenant_keys(f"/{made['id']}"), key=s.tk, json={"name": "  Agent   2 "}
        )
        empty = await req(app, "PATCH", tenant_keys(f"/{made['id']}"), key=s.tk, json={})
    assert before.status_code == 403
    assert patched.status_code == 200 and patched.json()["permission"] == "full"
    assert after.status_code == 200
    assert renamed.json()["name"] == "Agent 2"
    assert empty.status_code == 200  # nothing to change is not an error


async def test_another_owners_key_is_not_found(s: Setup) -> None:
    other = s.world.tenant("Pune Agency")
    pune_tk = s.add_key(other, "tk", f"tk_{str(other.id)[:8]}_pune")
    with auth_app(s.world) as app:
        pune_key = s.keys_of(other)[0]
        res = [
            await req(app, "PATCH", tenant_keys(f"/{pune_key.id}"), key=s.tk, json={"name": "x"}),
            await req(app, "POST", tenant_keys(f"/{pune_key.id}/rotate"), key=s.tk),
            await req(app, "DELETE", tenant_keys(f"/{pune_key.id}"), key=s.tk),
        ]
        still = await req(app, "GET", "/api/v1/whoami", key=pune_tk)
    assert [r.status_code for r in res] == [404, 404, 404]
    assert still.status_code == 200 and len(s.keys_of(other)) == 1


# ------------------------------------------------------------------ knowledge-base keys


async def test_a_knowledge_base_key_cannot_read_a_customers_keys_and_vice_versa(s: Setup) -> None:
    with auth_app(s.world) as app:
        ck_on_tenant = await req(app, "GET", tenant_keys(), key=s.ck)
        tk_on_corpus = await req(app, "GET", corpus_keys(s), key=s.tk)
    assert ck_on_tenant.status_code == 403  # a ck_ key is not a tenant
    assert tk_on_corpus.status_code == 200  # the customer's own knowledge base


async def test_create_knowledge_base_keys_with_the_customer_key_or_its_own(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = await req(
            app,
            "POST",
            corpus_keys(s),
            key=s.tk,
            json={"name": "Agent - n8n", "permission": "write"},
        )
        own = await req(app, "GET", corpus_keys(s), key=s.ck)
        agent = made.json()["api_key"]
        who = await req(app, "GET", "/api/v1/whoami", key=agent)
        denied = await req(app, "GET", corpus_keys(s), key=agent)
    assert made.status_code == 201, made.text
    assert made.json()["api_key"].startswith("ck_abcd1234_") or made.json()["api_key"].startswith(
        f"ck_{str(s.corpus.id)[:8]}_"
    )
    assert {k["name"] for k in own.json()["keys"]} == {"Default key", "Agent - n8n"}
    assert who.status_code == 200 and who.json()["kind"] == "corpus"
    assert denied.status_code == 403 and denied.json()["code"] == "key_permission_denied"
    (row,) = [k for k in s.keys_of(s.corpus) if k.name == "Agent - n8n"]
    assert row.id_short == str(s.corpus.id)[:8] and row.corpus_id == s.corpus.id
    assert row.tenant_id is None


async def test_a_corpus_key_cannot_manage_another_knowledge_bases_keys(s: Setup) -> None:
    other = s.world.db.add(
        Corpus(
            name="Other KB",
            tenant_id=s.tenant.id,
            qdrant_collection="corpus_ffff0000",
            api_key_hash="",
        )
    )
    with auth_app(s.world) as app:
        res = await req(app, "GET", f"/api/v1/corpora/{other.id}/keys", key=s.ck)
    assert res.status_code == 403


async def test_another_customers_key_cannot_reach_this_knowledge_base(s: Setup) -> None:
    other = s.world.tenant("Pune Agency")
    pune = s.add_key(other, "tk", f"tk_{str(other.id)[:8]}_pune")
    with auth_app(s.world) as app:
        res = await req(app, "GET", corpus_keys(s), key=pune)
        post = await req(app, "POST", corpus_keys(s), key=pune, json={"name": "x"})
    assert res.status_code == 403 and post.status_code == 403
    assert len(s.keys_of(s.corpus)) == 1


async def test_legacy_rotate_key_replaces_only_the_default_key(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = (await req(app, "POST", corpus_keys(s), key=s.tk, json={"name": "Agent"})).json()
        rotated = await req(app, "POST", f"/api/v1/corpora/{s.corpus.id}/rotate-key", key=ADMIN)
        old_default = await req(app, "GET", "/api/v1/whoami", key=s.ck)
        new_default = await req(app, "GET", "/api/v1/whoami", key=rotated.json()["api_key"])
        agent = await req(app, "GET", "/api/v1/whoami", key=made["api_key"])
    assert rotated.status_code == 200, rotated.text
    assert old_default.status_code == 401
    assert new_default.status_code == 200 and agent.status_code == 200
    assert len(s.keys_of(s.corpus)) == 2


async def test_legacy_rotate_issues_the_first_key_when_none_is_left(s: Setup) -> None:
    for key in s.keys_of(s.tenant):
        s.world.db._remove(key)
    with auth_app(s.world) as app:
        rotated = await req(
            app, "POST", f"/api/v1/admin/tenants/{s.tenant.id}/rotate-key", key=ADMIN
        )
        who = await req(app, "GET", "/api/v1/whoami", key=rotated.json()["api_key"])
    assert rotated.status_code == 200 and who.status_code == 200
    (row,) = s.keys_of(s.tenant)
    assert (row.name, row.permission, row.is_default) == ("Default key", "full", True)


async def test_deleting_a_knowledge_base_takes_its_keys(s: Setup) -> None:
    s.world.db._remove(s.corpus)
    assert s.keys_of(s.corpus) == []
    assert len(s.keys_of(s.tenant)) == 1


# ------------------------------------------------------------------ admin and sessions


async def test_admin_manages_a_customers_keys(s: Setup) -> None:
    base = f"/api/v1/admin/tenants/{s.tenant.id}/keys"
    with auth_app(s.world) as app:
        made = await req(app, "POST", base, key=ADMIN, json={"name": "Ops", "permission": "write"})
        listed = await req(app, "GET", base, key=ADMIN)
        deleted = await req(app, "DELETE", f"{base}/{made.json()['id']}", key=ADMIN)
        missing = await req(app, "GET", f"/api/v1/admin/tenants/{uuid.uuid4()}/keys", key=ADMIN)
        by_tenant = await req(app, "GET", base, key=s.tk)
    assert made.status_code == 201 and made.json()["created_by"] == "admin key"
    assert {k["name"] for k in listed.json()["keys"]} == {"Default key", "Ops"}
    assert deleted.status_code == 204 and missing.status_code == 404
    assert by_tenant.status_code == 403  # admin only


async def test_owner_session_manages_keys_and_a_member_does_not(s: Setup) -> None:
    asha = s.world.user("asha@shree.example")
    s.world.member(asha, s.tenant, "owner")
    ravi = s.world.user("ravi@shree.example")
    s.world.member(ravi, s.tenant, "member")
    with auth_app(s.world) as app:
        owner_token = (await login(app, asha.email)).json()["access_token"]
        member_token = (await login(app, ravi.email)).json()["access_token"]
        made = await req(
            app, "POST", tenant_keys(), token=owner_token, json={"name": "Dashboard-made"}
        )
        refused = await req(app, "GET", tenant_keys(), token=member_token)
        member_post = await req(app, "POST", tenant_keys(), token=member_token, json={"name": "x"})
        corpus_list = await req(app, "GET", corpus_keys(s), token=owner_token)
        member_corpus = await req(app, "GET", corpus_keys(s), token=member_token)
    assert made.status_code == 201 and made.json()["created_by"] == "asha@shree.example"
    assert refused.status_code == 403 and refused.json()["code"] == "owner_required"
    assert member_post.status_code == 403 and member_corpus.status_code == 403
    assert corpus_list.status_code == 200


async def test_suspended_customers_keys_are_all_refused(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = (await req(app, "POST", tenant_keys(), key=s.tk, json={"name": "Agent"})).json()
        s.tenant.status = "suspended"
        res = await req(app, "GET", "/api/v1/whoami", key=made["api_key"])
    assert res.status_code == 403 and res.json()["code"] == "workspace_not_active"


# ------------------------------------------------------------------ audit


async def test_every_change_is_audited_without_the_key(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = (
            await req(
                app, "POST", corpus_keys(s), key=s.tk, json={"name": "Agent", "permission": "read"}
            )
        ).json()
        await req(
            app, "PATCH", corpus_keys(s, f"/{made['id']}"), key=s.tk, json={"permission": "write"}
        )
        rotated = (await req(app, "POST", corpus_keys(s, f"/{made['id']}/rotate"), key=s.tk)).json()
        await req(app, "DELETE", corpus_keys(s, f"/{made['id']}"), key=s.tk)
    created = s.world.audits("api_key.created")
    updated = s.world.audits("api_key.updated")
    rotated_rows = s.world.audits("api_key.rotated")
    revoked = s.world.audits("api_key.revoked")
    assert [len(x) for x in (created, updated, rotated_rows, revoked)] == [1, 1, 1, 1]
    assert created[0].details["name"] == "Agent" and created[0].details["permission"] == "read"
    assert created[0].tenant_id == s.tenant.id and created[0].target_id == str(s.corpus.id)
    assert updated[0].details["changes"] == {"permission": {"from": "read", "to": "write"}}
    assert created[0].actor_kind == "tenant_key"
    assert "Default key" in created[0].actor_label
    everything = " ".join(str(a.details) for a in (*created, *updated, *rotated_rows, *revoked))
    assert made["api_key"] not in everything and rotated["api_key"] not in everything
    assert "hash" not in everything


async def test_the_actor_of_a_key_call_names_the_key(s: Setup) -> None:
    with auth_app(s.world) as app:
        agent = (
            await req(
                app,
                "POST",
                tenant_keys(),
                key=s.tk,
                json={"name": "Agent - n8n", "permission": "full"},
            )
        ).json()["api_key"]
        await req(app, "POST", tenant_keys(), key=agent, json={"name": "Child"})
    last = s.world.audits("api_key.created")[-1]
    assert last.actor_label == "tenant key Shree Labs (Agent - n8n)"


# ------------------------------------------------------------------ whoami


async def test_whoami_tells_a_key_its_permission_and_name(s: Setup) -> None:
    with auth_app(s.world) as app:
        made = (
            await req(
                app,
                "POST",
                corpus_keys(s),
                key=s.ck,
                json={"name": "Agent - Claude Code", "permission": "read"},
            )
        ).json()
        agent = await req(app, "GET", "/api/v1/whoami", key=made["api_key"])
        default = await req(app, "GET", "/api/v1/whoami", key=s.tk)
        admin = await req(app, "GET", "/api/v1/whoami", key=ADMIN)
    assert agent.json()["permission"] == "read"
    assert agent.json()["key_name"] == "Agent - Claude Code"
    assert agent.json()["key_id"] == made["id"]
    assert default.json()["permission"] == "full" and default.json()["key_name"] == "Default key"
    assert admin.json()["permission"] is None and admin.json()["key_name"] is None
    assert admin.json()["key_id"] is None
