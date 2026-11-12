"""Operations and security benchmark: isolation, abuse limits, latency, throughput, footprint.

    python benchmarks/ops.py --label gpu

Needs a running stack and RAG_ADMIN_API_KEY. No LLM key: every check here runs
without one. It creates two tenants with a corpus each, then:

  isolation   every key type (tenant, corpus, widget) and, when the engine has sign-ins
              (RAG_SESSION_SECRET), an owner's and a member's dashboard session try to
              read, change or delete the OTHER tenant's resources (Content Portal
              included when it is on); each attempt must be refused (401/403/404).
              Plus the account attacks (accounts A8): a member on owner-only routes of
              its own workspace, forged and replayed tokens, invitation abuse, a removed
              member, suspend safety, a suspended tenant's every credential, sign-up /
              forgot enumeration and the login lockout
  widget      wrong origin, no origin, secret key used as widget key, burst past the
              per-minute limit — each must be refused
  auth        what a sign-in costs per request: GET /api/v1/whoami with a corpus key
              vs a Bearer token (runs with --skip-load too; it takes seconds)
  latency     one request at a time on the FiQA corpus from benchmarks/beir.py, with
              the engine's DEFAULT retrieval settings (hybrid, final_k 5) — what a
              chatbot's retrieval step costs
  throughput  the same, N requests at once, for a fixed time
  footprint   memory per container (docker stats) and image sizes

Everything it creates is deleted at the end (an unverified sign-up made by the
enumeration check is left to the engine's hourly cleanup). Results land in
benchmarks/results/.
"""

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "clients" / "python"))
sys.path.insert(0, str(HERE))

from beir import CACHE, dataset_dir, load_qrels, read_jsonl  # noqa: E402
from cob_rag_client import CobRag  # noqa: E402
from run import BASE_RETRIEVAL, admin_key, bench_tenant, percentile  # noqa: E402

REFUSED = {401, 403, 404}
SITE = "https://shop-a.example"
# What a corpus gets when it sets nothing, pinned here so a server's own env file
# (which may predate these defaults) cannot change what "defaults" measures. The
# LLM-only steps (query classification, variants) skip themselves without a key.
SHIPPED = {
    "top_k": 20,
    "final_k": 5,
    "hybrid_search_enabled": True,
    "fusion_strategy": "convex",
    "hybrid_dense_weight": 0.75,
    "mmr_enabled": True,
    "temporal_decay_enabled": True,
    "reranking_enabled": False,
    "cache_enabled": False,
}


def call(
    api: str, key: str | None, method: str, path: str, *, bearer: str | None = None, **kw: Any
) -> httpx.Response:
    headers = dict(kw.pop("headers", {}))
    if key:
        headers["X-API-Key"] = key
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return httpx.request(method, api + path, headers=headers, timeout=120, **kw)


def _code(res: httpx.Response) -> str | None:
    """The error body's machine-readable `code`, if any."""
    try:
        body = res.json()
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


def _row(caller: str, attempt: str, status: Any, ok: bool | None) -> dict[str, Any]:
    """One result row. `refused` is True when the attempt was stopped, or for an
    "allowed"/"unchanged" check, when what must hold held."""
    return {"caller": caller, "attempt": attempt, "status": status, "refused": ok}


def _accounts(api: str) -> dict[str, Any]:
    """Public branding: whether sign-ins (and self sign-up) are on."""
    res = call(api, None, "GET", "/api/v1/public/branding")
    return res.json() if res.status_code == 200 else {}


def _join(api: str, tenant_id: str, email: str, role: str) -> dict[str, Any]:
    """The admin invites `email` into the tenant (the link comes back in `accept_url`,
    accounts A5) and the invitee accepts as a new account: its tokens, user and password."""
    invite = call(
        api,
        admin_key(),
        "POST",
        f"/api/v1/admin/tenants/{tenant_id}/invitations",
        json={"email": email, "role": role},
    )
    invite.raise_for_status()
    token = invite.json()["accept_url"].split("#token=", 1)[1]
    password = secrets.token_urlsafe(18)
    accepted = call(
        api,
        None,
        "POST",
        "/api/v1/invitations/accept",
        json={"token": token, "name": email.split("@")[0], "password": password},
    )
    accepted.raise_for_status()
    return {**accepted.json(), "email": email, "password": password}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _forge(
    token: str,
    *,
    claims: dict[str, Any] | None = None,
    alg: str | None = None,
    keep_signature: bool = False,
) -> str:
    """`token` with changed claims/header: re-signed with a random key (the attacker
    does not have the engine's), unsigned for `alg: none`, or with its original
    signature over the changed payload (`keep_signature`)."""
    head, body, signature = token.split(".")
    header = json.loads(_unb64(head))
    payload = {**json.loads(_unb64(body)), **(claims or {})}
    if alg:
        header["alg"] = alg
    h = _b64(json.dumps(header, separators=(",", ":")).encode())
    p = _b64(json.dumps(payload, separators=(",", ":")).encode())
    if alg == "none":
        return f"{h}.{p}."
    if keep_signature:
        return f"{h}.{p}.{signature}"
    mac = hmac.new(secrets.token_bytes(32), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64(mac)}"


def _portal_sessions_allowed(
    api: str, corpus_a: str, owner: dict[str, Any], member: dict[str, Any]
) -> list[dict[str, Any]]:
    """Sign-ins are ALLOWED on their own workspace's portal (accounts A0/A5): scoped, not
    refused. Only deleting the workspace is an owner's."""
    rows = []
    made = call(
        api,
        None,
        "POST",
        f"/api/v1/portal/workspaces?corpus_id={corpus_a}",
        bearer=owner["access_token"],
        json={"title": "A session docs"},
    )
    rows.append(
        _row(
            "owner A session",
            "portal on A's own knowledge base: create a workspace is allowed",
            made.status_code,
            made.status_code == 201,
        )
    )
    if made.status_code != 201:
        return rows
    ws = made.json()["id"]
    w = f"/api/v1/portal/workspaces/{ws}"
    for caller, person in (("owner A session", owner), ("member A session", member)):
        bearer = person["access_token"]
        listed = call(
            api, None, "GET", f"/api/v1/portal/workspaces?corpus_id={corpus_a}", bearer=bearer
        )
        items = listed.json().get("items", []) if listed.status_code == 200 else []
        rows.append(
            _row(
                caller,
                "portal on A's own workspace: list is allowed and shows it",
                listed.status_code,
                listed.status_code == 200 and any(i["id"] == ws for i in items),
            )
        )
        chunks = call(api, None, "GET", f"{w}/chunks", bearer=bearer)
        rows.append(
            _row(
                caller,
                "portal on A's own workspace: read chunks is allowed",
                chunks.status_code,
                chunks.status_code == 200,
            )
        )
    bearer = member["access_token"]
    call(api, None, "POST", f"{w}/lock", bearer=bearer, json={})
    added = call(api, None, "POST", f"{w}/chunks", bearer=bearer, json={"text": "A member's note"})
    call(api, None, "POST", f"{w}/unlock", bearer=bearer, json={})
    rows.append(
        _row(
            "member A session",
            "portal on A's own workspace: add a chunk is allowed",
            added.status_code,
            added.status_code == 201,
        )
    )
    deleted = call(api, None, "DELETE", w, bearer=bearer)
    still = call(api, None, "GET", w, bearer=owner["access_token"])
    rows.append(
        _row(
            "member A session",
            "portal on A's own workspace: DELETE the workspace (owner-only)",
            deleted.status_code,
            deleted.status_code == 403
            and _code(deleted) == "owner_required"
            and still.status_code == 200,
        )
    )
    return rows


def _member_owner_only(
    api: str,
    member: dict[str, Any],
    corpus_a: str,
    doc_a: str,
    version_a: str,
    pair_a: str,
    widget_a: str,
    label: Any,
) -> list[dict[str, Any]]:
    """A member of A on the owner-only routes of A's OWN resources (accounts A5, A8):
    each must be 403 `owner_required`."""
    attempts: list[tuple[str, str, dict[str, Any] | None]] = [
        ("POST", "/api/v1/corpora", {"name": "member KB"}),
        ("DELETE", f"/api/v1/corpora/{corpus_a}", None),
        ("POST", f"/api/v1/corpora/{corpus_a}/rotate-key", None),
        ("PUT", f"/api/v1/corpora/{corpus_a}", {"llm_config": {"model": "gpt-4o-mini"}}),
        ("DELETE", f"/api/v1/documents/{doc_a}?corpus_id={corpus_a}", None),
        (
            "POST",
            f"/api/v1/documents/{doc_a}/versions/{version_a}/restore?corpus_id={corpus_a}",
            None,
        ),
        ("DELETE", f"/api/v1/golden/{pair_a}", None),
        (
            "POST",
            f"/api/v1/corpora/{corpus_a}/widget-keys",
            {"name": "member site", "allowed_origins": ["https://member.example"]},
        ),
        ("POST", f"/api/v1/corpora/{corpus_a}/widget-keys/{widget_a}/rotate", None),
        ("DELETE", f"/api/v1/corpora/{corpus_a}/widget-keys/{widget_a}", None),
        ("POST", "/api/v1/tenants/me/rotate-key", None),
        ("POST", "/api/v1/tenants/me/members", {"email": "friend@ops.example", "role": "owner"}),
        ("DELETE", f"/api/v1/corpora/{corpus_a}/memories?user_id=u", None),
        ("DELETE", f"/api/v1/corpora/{corpus_a}/memories/purge", None),
    ]
    rows = []
    for method, path, body in attempts:
        kw = {"json": body} if body is not None else {}
        res = call(api, None, method, path, bearer=member["access_token"], **kw)
        rows.append(
            _row(
                "member A session, A's own",
                f"{method} {label(path)} (owner-only)",
                res.status_code,
                res.status_code == 403 and _code(res) == "owner_required",
            )
        )
    return rows


def _account_attacks(
    api: str,
    a: dict[str, Any],
    b: dict[str, Any],
    owner_a: dict[str, Any],
    owner_b: dict[str, Any],
    branding: dict[str, Any],
    suffix: str,
) -> list[dict[str, Any]]:
    """Admin routes from a tenant sign-in, forged and replayed tokens, invitation abuse,
    enumeration and the lockout (accounts A8)."""
    rows = []
    # An owner of A reaching B's team through the admin routes.
    for method, path, body in (
        ("GET", f"/api/v1/admin/tenants/{b['id']}/members", None),
        (
            "POST",
            f"/api/v1/admin/tenants/{b['id']}/invitations",
            {"email": f"mole-{suffix}@ops.example", "role": "owner"},
        ),
    ):
        kw = {"json": body} if body is not None else {}
        res = call(api, None, method, path, bearer=owner_a["access_token"], **kw)
        rows.append(
            _row(
                "owner A session",
                f"{method} {path.replace(b['id'], '{tenantB}')}",
                res.status_code,
                res.status_code == 403,
            )
        )

    # Forged access tokens: none may be accepted.
    token = owner_a["access_token"]
    forged = {
        "owner A's token re-signed with a wrong key": _forge(token),
        "owner A's token with tid changed to B (original signature)": _forge(
            token, claims={"tid": b["id"]}, keep_signature=True
        ),
        "owner A's token with tid changed to B, re-signed with a wrong key": _forge(
            token, claims={"tid": b["id"]}
        ),
        "owner A's token with alg: none": _forge(token, alg="none"),
        "owner A's token expired, signed with a wrong key": _forge(
            token, claims={"iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600}
        ),
    }
    for what, bad in forged.items():
        res = call(api, None, "GET", "/api/v1/corpora", bearer=bad)
        rows.append(
            _row(
                "forged token",
                f"GET /api/v1/corpora with {what}",
                res.status_code,
                res.status_code == 401,
            )
        )

    # Refresh-token reuse, on a second sign-in of owner A (the first stays usable).
    login = call(
        api,
        None,
        "POST",
        "/api/v1/auth/login",
        json={"email": owner_a["email"], "password": owner_a["password"]},
    )
    if login.status_code == 200:
        old = login.json()["refresh_token"]
        rotated = call(api, None, "POST", "/api/v1/auth/refresh", json={"refresh_token": old})
        new = rotated.json().get("refresh_token") if rotated.status_code == 200 else None
        replay = call(api, None, "POST", "/api/v1/auth/refresh", json={"refresh_token": old})
        rows.append(
            _row(
                "refresh reuse",
                "refresh owner A once, then present the OLD refresh token",
                replay.status_code,
                rotated.status_code == 200 and replay.status_code == 401,
            )
        )
        after = call(api, None, "POST", "/api/v1/auth/refresh", json={"refresh_token": new or ""})
        rows.append(
            _row(
                "refresh reuse",
                "then the NEW refresh token (the reuse revoked the session)",
                after.status_code,
                after.status_code == 401,
            )
        )
    else:
        rows.append(
            _row(
                "refresh reuse",
                f"skipped: login failed ({login.text[:80]})",
                login.status_code,
                False,
            )
        )

    # Invitation abuse: someone else's invitation, then a used one.
    invited = f"invitee-{suffix}@ops.example"
    invite = call(
        api,
        admin_key(),
        "POST",
        f"/api/v1/admin/tenants/{a['id']}/invitations",
        json={"email": invited, "role": "owner"},
    )
    inv_token = invite.json()["accept_url"].split("#token=", 1)[1]
    mismatch = call(
        api,
        None,
        "POST",
        "/api/v1/invitations/accept",
        bearer=owner_b["access_token"],
        json={"token": inv_token},
    )
    rows.append(
        _row(
            "owner B session",
            "accept an invitation into A addressed to someone else",
            mismatch.status_code,
            mismatch.status_code == 403 and _code(mismatch) == "invitation_email_mismatch",
        )
    )
    body = {"token": inv_token, "name": "Invitee", "password": secrets.token_urlsafe(18)}
    proper = call(api, None, "POST", "/api/v1/invitations/accept", json=body)
    again = call(api, None, "POST", "/api/v1/invitations/accept", json=body)
    rows.append(
        _row(
            "no key",
            "the same invitation token again after a proper accept",
            again.status_code,
            proper.status_code == 200
            and again.status_code == 404
            and _code(again) == "invitation_invalid",
        )
    )

    # Enumeration: an address with an account and a fresh one get the same answers.
    fresh = f"nobody-{suffix}@ops.example"
    if branding.get("signup_available"):
        answers = []
        for email in (owner_a["email"], fresh):
            res = call(
                api,
                None,
                "POST",
                "/api/v1/auth/signup",
                json={
                    "business_name": "Probe Labs",
                    "name": "Probe",
                    "email": email,
                    "password": secrets.token_urlsafe(18),
                },
            )
            answers.append((res.status_code, res.text.replace(email, "<email>")))
        rate_limited = any(status == 429 for status, _ in answers)
        rows.append(
            _row(
                "no key",
                "POST /api/v1/auth/signup: existing vs fresh address, same status and body"
                + (" (skipped: sign-up rate limit hit, rerun later)" if rate_limited else ""),
                [s for s, _ in answers],
                None if rate_limited else answers[0] == answers[1],
            )
        )
    else:
        rows.append(
            _row("no key", "sign-up enumeration skipped: signup_available is false", None, None)
        )
    answers = []
    for email in (owner_a["email"], fresh):
        res = call(api, None, "POST", "/api/v1/auth/forgot", json={"email": email})
        answers.append((res.status_code, res.text))
    rows.append(
        _row(
            "no key",
            "POST /api/v1/auth/forgot: existing vs fresh address, same status and body",
            [s for s, _ in answers],
            answers[0] == answers[1],
        )
    )

    # Lockout: the sixth wrong password in a row for one address is refused as locked.
    target = f"locked-{suffix}@ops.example"
    statuses = []
    last = None
    for _ in range(6):
        last = call(
            api,
            None,
            "POST",
            "/api/v1/auth/login",
            json={"email": target, "password": "wrong-password-" + suffix},
        )
        statuses.append(last.status_code)
    rows.append(
        _row(
            "no key",
            "6 wrong-password logins on one address: the 6th is 429 account_locked",
            statuses,
            statuses[:5] == [401] * 5 and statuses[5] == 429 and _code(last) == "account_locked",
        )
    )
    return rows


def _suspend_safety(api: str, b: dict[str, Any]) -> list[dict[str, Any]]:
    """Suspending needs the tenant's exact name (accounts A4): without it, nothing happens."""
    rows = []
    path = f"/api/v1/admin/tenants/{b['id']}"
    for what, method, sub, body in (
        ("suspend without confirm_name", "POST", "/suspend", {}),
        (
            "suspend with the name in the wrong case",
            "POST",
            "/suspend",
            {"confirm_name": b["name"].upper()},
        ),
        ('PUT {"is_active": false} without confirm_name', "PUT", "", {"is_active": False}),
    ):
        res = call(api, admin_key(), method, path + sub, json=body)
        rows.append(_row("admin key", f"{what}: 422", res.status_code, res.status_code == 422))
    status = call(api, admin_key(), "GET", path).json().get("status")
    rows.append(
        _row("admin key", "B still active after the three attempts", status, status == "active")
    )
    return rows


def isolation(api: str, admin: CobRag) -> list[dict[str, Any]]:
    """Tenant A's keys and sign-ins against tenant B's resources, and the account
    attacks. Every row must be refused (or, for a check, hold)."""
    suffix = secrets.token_hex(3)
    a = admin.create_tenant(f"ops-a-{int(time.time())}-{suffix}")
    b = admin.create_tenant(f"ops-b-{int(time.time())}-{suffix}")
    ca = admin.create_corpus("ops corpus A", tenant_id=a["id"])
    cb = admin.create_corpus("ops corpus B", tenant_id=b["id"])
    rag_b = CobRag(api, api_key=cb["api_key"], timeout=300)
    job_b = rag_b.ingest_records(
        [
            {"id": f"b-{i}", "title": "Tenant B secret", "text": f"Confidential fact {i}."}
            for i in range(5)
        ],
        wait=True,
        poll_interval=0.5,
    )
    docs_b = rag_b._req("GET", "/api/v1/documents")["items"]
    wk_b = rag_b.create_widget_key(cb["id"], "B site", ["https://shop-b.example"])
    b_corpus, b_doc, b_job, b_widget = cb["id"], docs_b[0]["id"], job_b["id"], wk_b["id"]
    # B's golden pair and a pending embedding migration (nothing runs until an admin
    # approves it), to attack the routes that address them by their own id.
    b_pair = rag_b._req(
        "POST",
        "/api/v1/golden",
        json={"corpus_id": cb["id"], "query": "Which fact?", "expected_answer": "Fact 1."},
    )["id"]
    catalog = call(api, admin_key(), "GET", "/api/v1/models/embeddings").json()
    info = call(api, admin_key(), "GET", "/api/v1/info").json()
    engine_model = info["embedding"]["model"]
    other_model = next(e for e in catalog if e["model"] != engine_model)
    b_migration = admin.update_corpus(
        cb["id"],
        embedding_config={"provider": other_model["provider"], "model": other_model["model"]},
    )["migration_id"]
    # A past query of B's, to attack query history and feedback. Recording one needs
    # an answer model; without one configured those two attacks are reported skipped.
    try:
        b_query: str | None = rag_b.query("Which fact is confidential?").get("query_id")
    except Exception:
        b_query = None
    b_version = rag_b._req("GET", f"/api/v1/documents/{b_doc}/versions")["versions"][0]["id"]

    # A's own resources, for the member's owner-only attempts (accounts A5, A8).
    rag_a = CobRag(api, api_key=ca["api_key"], timeout=300)
    rag_a.ingest_records(
        [{"id": "a-1", "title": "Tenant A", "text": "Our own fact."}], wait=True, poll_interval=0.5
    )
    a_doc = rag_a._req("GET", "/api/v1/documents")["items"][0]["id"]
    a_version = rag_a._req("GET", f"/api/v1/documents/{a_doc}/versions")["versions"][0]["id"]
    a_pair = rag_a._req(
        "POST",
        "/api/v1/golden",
        json={"corpus_id": ca["id"], "query": "Our fact?", "expected_answer": "Own fact."},
    )["id"]
    wk_a = rag_a.create_widget_key(ca["id"], "A site", [SITE])

    # Dashboard sign-ins (accounts A3/A5), when the engine has RAG_SESSION_SECRET: an
    # owner and a member of A and an owner of B, each through an admin invitation.
    branding = _accounts(api)
    accounts_on = bool(branding.get("accounts_enabled"))
    owner_a = member_a = owner_b = None
    if accounts_on:
        owner_a = _join(api, a["id"], f"owner-a-{suffix}@ops.example", "owner")
        member_a = _join(api, a["id"], f"member-a-{suffix}@ops.example", "member")
        owner_b = _join(api, b["id"], f"owner-b-{suffix}@ops.example", "owner")

    attempts = [
        ("GET", f"/api/v1/corpora/{b_corpus}", None),
        ("PUT", f"/api/v1/corpora/{b_corpus}", {"json": {"name": "hijacked"}}),
        ("POST", f"/api/v1/corpora/{b_corpus}/rotate-key", None),
        ("DELETE", f"/api/v1/corpora/{b_corpus}", None),
        (
            "POST",
            "/api/v1/query/retrieve",
            {"json": {"query": "confidential", "corpus_id": b_corpus}},
        ),
        ("POST", "/api/v1/query", {"json": {"query": "confidential", "corpus_id": b_corpus}}),
        (
            "POST",
            "/api/v1/ingest/records",
            {"json": {"corpus_id": b_corpus, "records": [{"id": "x", "text": "y"}]}},
        ),
        ("GET", f"/api/v1/ingest/jobs/{b_job}", None),
        ("DELETE", f"/api/v1/documents/{b_doc}?corpus_id={b_corpus}", None),
        ("GET", f"/api/v1/corpora/{b_corpus}/widget-keys", None),
        (
            "POST",
            f"/api/v1/corpora/{b_corpus}/widget-keys",
            {"json": {"name": "x", "allowed_origins": ["https://evil.example"]}},
        ),
        ("DELETE", f"/api/v1/corpora/{b_corpus}/widget-keys/{b_widget}", None),
        ("GET", f"/api/v1/usage?corpus_id={b_corpus}", None),
        ("GET", f"/api/v1/admin/tenants/{b['id']}", None),
        ("POST", f"/api/v1/admin/tenants/{b['id']}/rotate-key", None),
        # Routes an admin or tenant key reaches by naming the corpus (dashboard epic 0).
        ("GET", f"/api/v1/documents?corpus_id={b_corpus}", None),
        ("GET", f"/api/v1/documents/{b_doc}?corpus_id={b_corpus}", None),
        ("GET", f"/api/v1/config?corpus_id={b_corpus}", None),
        (
            "PATCH",
            f"/api/v1/config?corpus_id={b_corpus}",
            {"json": {"prompt_config": {"system_prompt": "hijacked"}}},
        ),
        ("GET", f"/api/v1/corpora/{b_corpus}/conversations", None),
        ("POST", f"/api/v1/corpora/{b_corpus}/conversations", {"json": {"title": "x"}}),
        ("GET", f"/api/v1/corpora/{b_corpus}/memories", None),
        (
            "POST",
            f"/api/v1/corpora/{b_corpus}/memories",
            {"json": {"content": "planted", "user_id": "u"}},
        ),
        ("GET", f"/api/v1/ingest/jobs?corpus_id={b_corpus}", None),
        ("GET", f"/api/v1/feedback?corpus_id={b_corpus}", None),
        ("GET", f"/api/v1/migrations?corpus_id={b_corpus}", None),
        ("GET", f"/api/v1/corpora/{b_corpus}/migrations", None),
        ("GET", f"/api/v1/migrations/{b_migration}", None),
        ("POST", f"/api/v1/migrations/{b_migration}/approve", None),
        ("PATCH", f"/api/v1/golden/{b_pair}", {"json": {"expected_answer": "hijacked"}}),
        ("GET", f"/api/v1/corpora?tenant_id={b['id']}", None),
        ("PUT", f"/api/v1/corpora/{ca['id']}", {"json": {"tenant_id": b["id"]}}),
        # accounts A8: B's version restore and golden-pair delete (owner-only routes)
        (
            "POST",
            f"/api/v1/documents/{b_doc}/versions/{b_version}/restore?corpus_id={b_corpus}",
            None,
        ),
        ("DELETE", f"/api/v1/golden/{b_pair}", None),
        # engine-wide operator routes: webhook subscriptions and the audit trail
        ("GET", "/api/v1/webhooks", None),
        (
            "POST",
            "/api/v1/webhooks",
            {"json": {"url": "https://evil.example/hook", "events": ["config.changed"]}},
        ),
        ("GET", f"/api/v1/admin/audit?tenant_id={b['id']}", None),
    ]
    # The Content Portal (when the engine mounts it): B's workspace with one staged
    # text item, attacked through every kind of portal route (G-13, accounts epic A0).
    portal_on = bool(info.get("features", {}).get("content_portal"))
    ws_b = ws_a = review_b = rule_b = None
    wr = ""
    portal_ids: dict[str, str] = {}
    if portal_on:
        ws_b = rag_b._req("POST", "/api/v1/portal/workspaces", json={"title": "B docs"})["id"]
        w = f"/api/v1/portal/workspaces/{ws_b}"
        content_b = rag_b._req(
            "POST", f"{w}/content", json={"source_type": "text_dump", "content": "Tenant B secret"}
        )["id"]
        # Two staged chunks of B's and a content rule, to name from A's own workspace.
        rag_b._req("POST", f"{w}/lock", json={"user": "b-owner"})
        chunk_b, chunk_b2 = (
            rag_b._req("POST", f"{w}/chunks", json={"text": text, "user": "b-owner"})["id"]
            for text in ("Tenant B chunk one", "Tenant B chunk two")
        )
        rag_b._req("POST", f"{w}/unlock", json={"user": "b-owner"})
        rule_b = rag_b._req(
            "POST",
            f"/api/v1/portal/corpus/{b_corpus}/rules",
            json={"rule_type": "always", "condition": {}, "action": "stage", "priority": 5},
        )["id"]
        # B's post-ingest review of its document: finalizing it would publish a version.
        review_b = rag_b._req(
            "POST",
            "/api/v1/portal/workspaces/from-ingestion",
            json={"document_version_id": b_version},
        )["workspace_id"]
        # A's own workspace, locked by the attacker, so the only thing standing between
        # A's keys and B's objects named inside it is each object's own check.
        ws_a = rag_a._req("POST", "/api/v1/portal/workspaces", json={"title": "A docs"})["id"]
        wa = f"/api/v1/portal/workspaces/{ws_a}"
        rag_a._req("POST", f"{wa}/lock", json={"user": "attacker"})
        wr = f"/api/v1/portal/workspaces/{review_b}"
        as_attacker = {"json": {"user": "attacker"}}
        rewrite_b = {
            "json": {
                "action": {
                    "tool_name": "rewrite_chunk",
                    "arguments": {"chunk_id": chunk_b, "new_text": "hijacked"},
                },
                "user": "attacker",
            }
        }
        portal_ids = {
            ws_a: "{workspaceA}",
            content_b: "{contentB}",
            chunk_b: "{chunkB}",
            chunk_b2: "{chunkB2}",
            rule_b: "{ruleB}",
            review_b: "{reviewB}",
        }
        attempts += [
            ("GET", w, None),
            ("PATCH", w, {"json": {"title": "hijacked"}}),
            ("DELETE", w, None),
            ("POST", f"{w}/lock", as_attacker),
            ("GET", f"{w}/content", None),
            ("POST", f"{w}/content", {"json": {"source_type": "text_dump", "content": "planted"}}),
            ("GET", f"{w}/chunks", None),
            ("POST", f"{w}/chunks", {"json": {"text": "planted", "user": "attacker"}}),
            ("POST", f"{w}/submit", as_attacker),
            ("POST", f"{w}/approve", as_attacker),
            ("POST", f"{w}/ingest", as_attacker),
            ("POST", f"{w}/chat", {"json": {"message": "What is in here?", "user": "attacker"}}),
            ("GET", f"{w}/audit-trail", None),
            ("GET", f"/api/v1/portal/corpus/{b_corpus}/rules", None),
            (
                "POST",
                f"/api/v1/portal/corpus/{b_corpus}/rules",
                {"json": {"rule_type": "always", "condition": {}, "action": "direct_ingest"}},
            ),
            (
                "POST",
                "/api/v1/portal/workspaces/from-ingestion",
                {"json": {"document_version_id": b_version, "user": "attacker"}},
            ),
            # B's review and chat actions.
            ("POST", f"{wr}/finalize-review", as_attacker),
            ("GET", f"{wr}/review-status", None),
            ("POST", f"{w}/chat/execute", rewrite_b),
            # A's OWN workspace and corpus in the path, B's objects by id.
            ("GET", f"{wa}/content/{content_b}", None),
            ("POST", f"{wa}/content/{content_b}/reprocess", None),
            ("PATCH", f"{wa}/chunks/{chunk_b}", {"json": {"text": "hijacked", "user": "attacker"}}),
            ("POST", f"{wa}/chunks/{chunk_b}/split", {"json": {"position": 6, "user": "attacker"}}),
            (
                "POST",
                f"{wa}/chunks/merge",
                {"json": {"chunk_ids": [chunk_b, chunk_b2], "user": "attacker"}},
            ),
            (
                "POST",
                f"{wa}/chunks/reorder",
                {"json": {"chunk_ids": [chunk_b2, chunk_b], "user": "attacker"}},
            ),
            ("POST", f"{wa}/chunks/{chunk_b}/approve", as_attacker),
            ("POST", f"{wa}/chunks/{chunk_b}/reject", as_attacker),
            ("DELETE", f"{wa}/chunks/{chunk_b}?user=attacker", None),
            ("POST", f"{wa}/chat/execute", rewrite_b),
            (
                "PATCH",
                f"/api/v1/portal/corpus/{ca['id']}/rules/{rule_b}",
                {"json": {"priority": 1}},
            ),
            ("DELETE", f"/api/v1/portal/corpus/{ca['id']}/rules/{rule_b}", None),
        ]
    if b_query:
        attempts += [
            ("GET", f"/api/v1/query/{b_query}", None),
            ("POST", "/api/v1/feedback", {"json": {"query_id": b_query, "rating": 1}}),
        ]
    names = {
        b_corpus: "{B}",
        b_doc: "{doc}",
        b_job: "{job}",
        b_widget: "{widget}",
        b["id"]: "{tenantB}",
        ca["id"]: "{A}",
        b_pair: "{pair}",
        b_migration: "{migration}",
        b_version: "{versionB}",
        a_doc: "{docA}",
        a_version: "{versionA}",
        a_pair: "{pairA}",
        wk_a["id"]: "{widgetA}",
        a["id"]: "{tenantA}",
    }
    if b_query:
        names[b_query] = "{query}"
    if ws_b:
        names[ws_b] = "{workspaceB}"
        names.update(portal_ids)

    def _label(path: str) -> str:
        for value, name in names.items():
            path = path.replace(value, name)
        return path

    # (caller, X-API-Key, Bearer token)
    callers: list[tuple[str, str | None, str | None]] = [
        ("tenant A key", a["api_key"], None),
        ("corpus A key", ca["api_key"], None),
        ("no key", None, None),
    ]
    if owner_a and member_a:
        callers += [
            ("owner A session", None, owner_a["access_token"]),
            ("member A session", None, member_a["access_token"]),
        ]
    rows = []
    lock_holder: tuple[str | None, str | None] = (ca["api_key"], None)
    for caller, key, bearer in callers:
        if ws_a and bearer:
            # A sign-in acts as its own user in the portal (A0 `portal_actor`), so it takes
            # A's workspace lock over from the key's "attacker": then only each object's own
            # check stands between it and B's objects named inside A's workspace.
            wa_path = f"/api/v1/portal/workspaces/{ws_a}"
            release = {"user": "attacker"} if lock_holder[1] is None else {}
            call(
                api,
                lock_holder[0],
                "POST",
                f"{wa_path}/unlock",
                bearer=lock_holder[1],
                json=release,
            )
            call(api, None, "POST", f"{wa_path}/lock", bearer=bearer, json={})
            lock_holder = (None, bearer)
        for method, path, kw in attempts:
            res = call(api, key, method, path, bearer=bearer, **(kw or {}))
            rows.append(
                {
                    "caller": caller,
                    "attempt": f"{method} {_label(path)}",
                    "status": res.status_code,
                    "refused": res.status_code in REFUSED,
                }
            )
    if not accounts_on:
        rows.append(
            _row(
                "owner / member A session",
                "session attacks skipped: sign-ins are off (no RAG_SESSION_SECRET)",
                None,
                None,
            )
        )
    # A widget (public) key must not work as an API key at all.
    for method, path, kw in attempts[:5]:
        res = call(api, None, method, path, headers={"X-API-Key": wk_a["public_key"]}, **(kw or {}))
        rows.append(
            {
                "caller": "widget key as API key",
                "attempt": f"{method} {path.replace(b_corpus, '{B}')}",
                "status": res.status_code,
                "refused": res.status_code in REFUSED,
            }
        )
    # /documents with another corpus's id is refused outright (above); and none of
    # B's documents may ever appear in what the corpus A key does get back.
    listed_docs = call(api, ca["api_key"], "GET", f"/api/v1/documents?corpus_id={b_corpus}")
    own = (
        {d["id"] for d in listed_docs.json().get("items", [])}
        if listed_docs.status_code == 200
        else set()
    )
    rows.append(
        {
            "caller": "corpus A key",
            "attempt": "GET /api/v1/documents?corpus_id={B} returns B's documents",
            "status": listed_docs.status_code,
            "refused": not own & {d["id"] for d in docs_b},
        }
    )
    # Listing must not leak B's corpus to A.
    listed = call(api, a["api_key"], "GET", "/api/v1/corpora").json()
    leaked = any(c["id"] == b_corpus for c in listed.get("items", []))
    rows.append(
        {
            "caller": "tenant A key",
            "attempt": "GET /api/v1/corpora lists {B}",
            "status": 200,
            "refused": not leaked,
        }
    )
    # And B's data must never come back in A's search.
    hits = CobRag(api, api_key=ca["api_key"]).retrieve(
        "Confidential fact", options={"final_k": 10}
    )["chunks"]
    rows.append(
        {
            "caller": "corpus A key",
            "attempt": "retrieve returns tenant B text",
            "status": 200,
            "refused": not any("Confidential" in h["text"] for h in hits),
        }
    )

    if not b_query:
        rows.append(
            {
                "caller": "tenant A / corpus A key",
                "attempt": "GET /api/v1/query/{query} and POST /api/v1/feedback (skipped: no model)",
                "status": None,
                "refused": None,
            }
        )
    if portal_on:
        # Listing workspaces must never return B's, for any of A's credentials.
        for caller, key, bearer in callers:
            if key is None and bearer is None:
                continue
            listed_ws = call(api, key, "GET", "/api/v1/portal/workspaces", bearer=bearer)
            items = listed_ws.json().get("items", []) if listed_ws.status_code == 200 else []
            rows.append(
                {
                    "caller": caller,
                    "attempt": "GET /api/v1/portal/workspaces lists {workspaceB}",
                    "status": listed_ws.status_code,
                    "refused": not any(item["id"] == ws_b for item in items),
                }
            )
        if owner_a and member_a:
            rows += _portal_sessions_allowed(api, ca["id"], owner_a, member_a)
    else:
        rows.append(
            {
                "caller": "tenant A / corpus A key",
                "attempt": "portal attacks skipped: RAG_ENABLE_CONTENT_PORTAL is false",
                "status": None,
                "refused": None,
            }
        )
    if owner_a and member_a and owner_b:
        rows += _member_owner_only(
            api, member_a, ca["id"], a_doc, a_version, a_pair, wk_a["id"], _label
        )
        rows += _account_attacks(api, a, b, owner_a, owner_b, branding, suffix)
    # Nothing any refused attempt tried may have changed B's data or moved A, and the
    # member's owner-only attempts changed nothing of A's.
    b_after = admin.get_corpus(b_corpus)
    a_after = admin.get_corpus(ca["id"])
    pair_after = call(api, admin_key(), "GET", f"/api/v1/golden?corpus_id={b_corpus}").json()
    rows.append(
        {
            "caller": "all of the above",
            "attempt": "B's config and golden pair unchanged, A still owned by tenant A",
            "status": 200,
            "refused": (
                (b_after.get("prompt_config") or {}).get("system_prompt") != "hijacked"
                and a_after.get("tenant_id") == a["id"]
                and [p["id"] for p in pair_after["items"]] == [b_pair]
                and all(p["expected_answer"] != "hijacked" for p in pair_after["items"])
            ),
        }
    )
    a_docs_after = call(api, ca["api_key"], "GET", "/api/v1/documents").json().get("items", [])
    a_pairs_after = call(api, admin_key(), "GET", f"/api/v1/golden?corpus_id={ca['id']}").json()
    a_keys_after = call(api, ca["api_key"], "GET", f"/api/v1/corpora/{ca['id']}/widget-keys")
    rows.append(
        _row(
            "all of the above",
            "A's document, golden pair and widget key still there, A's key still works",
            a_keys_after.status_code,
            a_keys_after.status_code == 200
            and [d["id"] for d in a_docs_after] == [a_doc]
            and [p["id"] for p in a_pairs_after["items"]] == [a_pair]
            and [k["id"] for k in a_keys_after.json()] == [wk_a["id"]],
        )
    )
    if portal_on:
        ws_after = call(api, admin_key(), "GET", f"/api/v1/portal/workspaces/{ws_b}")
        content_after = call(api, admin_key(), "GET", f"/api/v1/portal/workspaces/{ws_b}/content")
        chunks_after = call(api, admin_key(), "GET", f"/api/v1/portal/workspaces/{ws_b}/chunks")
        rules_after = call(api, admin_key(), "GET", f"/api/v1/portal/corpus/{b_corpus}/rules")
        rows.append(
            {
                "caller": "all of the above",
                "attempt": "B's workspace, staged content, chunks and rules unchanged",
                "status": ws_after.status_code,
                "refused": (
                    ws_after.status_code == 200
                    and ws_after.json()["title"] == "B docs"
                    and ws_after.json()["status"] == "draft"
                    and ws_after.json()["locked_by"] is None
                    and content_after.json()["count"] == 1
                    and sorted(c["text"] for c in chunks_after.json()["items"])
                    == ["Tenant B chunk one", "Tenant B chunk two"]
                    and [(r["id"], r["priority"]) for r in rules_after.json()["items"]]
                    == [(rule_b, 5)]
                ),
            }
        )
        review_after = call(api, admin_key(), "GET", f"{wr}/review-status")
        versions_after = call(
            api, admin_key(), "GET", f"/api/v1/documents/{b_doc}/versions?corpus_id={b_corpus}"
        )
        active = [
            v["id"] for v in versions_after.json().get("versions", []) if v["status"] == "active"
        ]
        rows.append(
            {
                "caller": "all of the above",
                "attempt": "B's review still in review, B's document version still the active one",
                "status": review_after.status_code,
                "refused": (
                    review_after.status_code == 200
                    and review_after.json()["status"] == "in_review"
                    and review_after.json()["finalized_version_id"] is None
                    and active == [b_version]
                ),
            }
        )
    if member_a:
        # A member removed by the admin: the access token still in their hands stops.
        removed = call(
            api,
            admin_key(),
            "DELETE",
            f"/api/v1/admin/tenants/{a['id']}/members/{member_a['user']['id']}",
        )
        after_removal = call(api, None, "GET", "/api/v1/corpora", bearer=member_a["access_token"])
        # Removal also revokes their sessions in the workspace (accounts A5), so the token is
        # refused as a revoked session (401); were the session kept, the per-request
        # membership check would answer 403 not_member.
        rows.append(
            _row(
                "removed member A (admin removed them)",
                "GET /api/v1/corpora with the access token issued before",
                f"{after_removal.status_code} {_code(after_removal)}",
                removed.status_code == 204
                and (after_removal.status_code, _code(after_removal))
                in {(401, "session_revoked"), (403, "not_member")},
            )
        )
    rows += _suspend_safety(api, b)
    # Suspending tenant B must switch off every credential of it at once.
    if owner_b:
        before = call(api, None, "GET", "/api/v1/corpora", bearer=owner_b["access_token"])
        rows.append(
            _row(
                "owner B session, before the suspension",
                "GET /api/v1/corpora is allowed (the token below is a live one)",
                before.status_code,
                before.status_code == 200,
            )
        )
    suspended = call(
        api,
        admin_key(),
        "POST",
        f"/api/v1/admin/tenants/{b['id']}/suspend",
        json={"confirm_name": b["name"]},
    )
    rows.append(
        _row(
            "admin key",
            "POST /api/v1/admin/tenants/{tenantB}/suspend with B's exact name suspends it",
            suspended.status_code,
            suspended.status_code == 200 and suspended.json().get("status") == "suspended",
        )
    )
    after = call(api, cb["api_key"], "GET", "/api/v1/documents")
    rows.append(
        _row(
            "corpus B key, tenant B suspended",
            "GET /api/v1/documents",
            after.status_code,
            after.status_code == 403 and _code(after) == "workspace_not_active",
        )
    )
    tk_after = call(api, b["api_key"], "GET", "/api/v1/tenants/me")
    rows.append(
        _row(
            "tenant B key, tenant B suspended",
            "GET /api/v1/tenants/me",
            tk_after.status_code,
            tk_after.status_code == 403 and _code(tk_after) == "workspace_not_active",
        )
    )
    widget_after = call(
        api,
        None,
        "POST",
        "/api/v1/widget/chat",
        headers={"X-Widget-Key": wk_b["public_key"], "Origin": "https://shop-b.example"},
        json={"message": "Which fact is confidential?"},
    )
    rows.append(
        _row(
            "widget key B from its own site, tenant B suspended",
            "POST /api/v1/widget/chat",
            widget_after.status_code,
            widget_after.status_code == 403,
        )
    )
    if owner_b:
        session_after = call(api, None, "GET", "/api/v1/corpora", bearer=owner_b["access_token"])
        rows.append(
            _row(
                "owner B session issued before the suspension",
                "GET /api/v1/corpora",
                session_after.status_code,
                session_after.status_code == 401 and _code(session_after) == "session_revoked",
            )
        )

    # A hard delete removes each tenant's corpora (vectors and files) too.
    for tenant in (a, b):
        call(
            api,
            admin_key(),
            "DELETE",
            f"/api/v1/admin/tenants/{tenant['id']}",
            params={"confirm_name": tenant["name"]},
        )
    return rows


def widget_abuse(api: str, admin: CobRag) -> list[dict[str, Any]]:
    # LLM-dependent steps off: this measures the limiter, not the model's speed.
    corpus = admin.create_corpus(
        "ops widget", tenant_id=bench_tenant(admin), retrieval_config=BASE_RETRIEVAL
    )
    rag = CobRag(api, api_key=corpus["api_key"], timeout=300)
    rag.ingest_records(
        [{"id": "r", "title": "Returns", "text": "30 days."}], wait=True, poll_interval=0.5
    )
    key = rag.create_widget_key(corpus["id"], "shop", [SITE], rate_limit_per_minute=5)
    pk = key["public_key"]
    rows = []

    def check(name: str, res: httpx.Response, expect: set[int]) -> None:
        rows.append({"check": name, "status": res.status_code, "ok": res.status_code in expect})

    check(
        "config from the allowed site",
        call(
            api, None, "GET", "/api/v1/widget/config", headers={"X-Widget-Key": pk, "Origin": SITE}
        ),
        {200},
    )
    check(
        "config from another site",
        call(
            api,
            None,
            "GET",
            "/api/v1/widget/config",
            headers={"X-Widget-Key": pk, "Origin": "https://evil.example"},
        ),
        {403},
    )
    check(
        "config with no Origin",
        call(api, None, "GET", "/api/v1/widget/config", headers={"X-Widget-Key": pk}),
        {403},
    )
    check(
        "secret corpus key used as widget key",
        call(
            api,
            None,
            "GET",
            "/api/v1/widget/config",
            headers={"X-Widget-Key": corpus["api_key"], "Origin": SITE},
        ),
        {401, 403},
    )

    # A burst: 8 messages at once, as a script hammering the widget would send them.
    # Only the status matters, so the streamed answer is not read.
    def post(_: int) -> int:
        with httpx.stream(
            "POST",
            api + "/api/v1/widget/chat",
            headers={"X-Widget-Key": pk, "Origin": SITE},
            json={"message": "returns?"},
            timeout=600,
        ) as res:
            return res.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = sorted(pool.map(post, range(8)))
    rows.append(
        {"check": "8 messages at a 5/min limit", "status": statuses, "ok": statuses.count(429) == 3}
    )
    admin.delete_corpus(corpus["id"], corpus["name"])
    return rows


def _fiqa(n: int) -> tuple[CobRag, list[str]]:
    entry = json.loads((CACHE / "state.json").read_text("utf-8"))["fiqa"]
    folder = dataset_dir("fiqa")
    qrels = load_qrels(folder / "qrels" / "test.tsv")
    queries = {str(q["_id"]): q["text"] for q in read_jsonl(folder / "queries.jsonl")}
    return entry, [queries[q] for q in qrels if q in queries][:n]


def latency_and_throughput(
    api: str, admin: CobRag, seconds: float, rerank_queries: int
) -> dict[str, Any]:
    entry, queries = _fiqa(200)
    rag = CobRag(api, api_key=entry["api_key"], timeout=300)

    def timed(config: dict[str, Any], n: int = len(queries)) -> dict[str, int]:
        admin.update_corpus(entry["id"], retrieval_config=config)
        for q in queries[:5]:
            rag.retrieve(q)  # warm caches and models
        times = []
        for q in queries[:n]:
            t = time.perf_counter()
            rag.retrieve(q)
            times.append((time.perf_counter() - t) * 1000)
        return {
            "p50": round(statistics.median(times)),
            "p95": round(percentile(times, 95)),
            "p99": round(percentile(times, 99)),
            "n": len(times),
        }

    with_rerank = timed({**SHIPPED, "reranking_enabled": True}, rerank_queries)
    out: dict[str, Any] = {
        "corpus": "FiQA-2018 (57,638 documents)",
        "settings": "engine defaults (hybrid, convex fusion, MMR, top_k 20, final_k 5)",
        "latency_ms": timed(SHIPPED),
        "latency_ms_with_reranker": with_rerank,
        "throughput": [],
    }
    for workers in (1, 4, 16):
        done: list[float] = []
        deadline = time.perf_counter() + seconds

        def worker(
            offset: int, deadline: float = deadline, done: list[float] = done, step: int = workers
        ) -> None:
            i = offset
            while time.perf_counter() < deadline:
                t = time.perf_counter()
                rag.retrieve(queries[i % len(queries)])
                done.append((time.perf_counter() - t) * 1000)
                i += step

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(worker, range(workers)))
        out["throughput"].append(
            {
                "concurrent": workers,
                "qps": round(len(done) / seconds, 1),
                "p95_ms": round(percentile(done, 95)),
            }
        )
    return out


def auth_overhead(api: str, admin: CobRag, n: int = 200) -> dict[str, Any]:
    """p50/p95 of GET /api/v1/whoami, one at a time over one connection, with a corpus
    key (verified once, then cached) and with a sign-in's Bearer token (verified per
    request: signature, then one indexed join of session, user, membership and tenant)."""
    if not _accounts(api).get("accounts_enabled"):
        return {"skipped": "sign-ins are off (no RAG_SESSION_SECRET)"}
    suffix = secrets.token_hex(3)
    tenant = admin.create_tenant(f"ops-auth-{suffix}")
    try:
        corpus = admin.create_corpus("ops auth", tenant_id=tenant["id"])
        owner = _join(api, tenant["id"], f"owner-auth-{suffix}@ops.example", "owner")
        with httpx.Client(base_url=api, timeout=60) as client:

            def timed(headers: dict[str, str]) -> dict[str, float]:
                for _ in range(10):  # warm the key cache and the connection
                    client.get("/api/v1/whoami", headers=headers).raise_for_status()
                times = []
                for _ in range(n):
                    t = time.perf_counter()
                    client.get("/api/v1/whoami", headers=headers).raise_for_status()
                    times.append((time.perf_counter() - t) * 1000)
                return {
                    "p50": round(statistics.median(times), 1),
                    "p95": round(percentile(times, 95), 1),
                }

            return {
                "n": n,
                "corpus_key_ms": timed({"X-API-Key": corpus["api_key"]}),
                "bearer_ms": timed({"Authorization": f"Bearer {owner['access_token']}"}),
            }
    finally:
        call(
            api,
            admin_key(),
            "DELETE",
            f"/api/v1/admin/tenants/{tenant['id']}",
            params={"confirm_name": tenant["name"]},
        )


def footprint() -> dict[str, Any]:
    def run(cmd: list[str]) -> str:
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, check=True
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    stats = [
        json.loads(x)
        for x in run(["docker", "stats", "--no-stream", "--format", "{{json .}}"]).splitlines()
        if x.strip()
    ]
    images = [
        json.loads(x)
        for x in run(["docker", "images", "--format", "{{json .}}"]).splitlines()
        if x.strip()
    ]
    return {
        "containers": [
            {
                "name": s["Name"].removeprefix("cob-rag-").removesuffix("-1"),
                "memory": s["MemUsage"].split(" / ")[0],
            }
            for s in stats
            if s["Name"].startswith("cob-rag-")
        ],
        "images": [
            {"image": f"{i['Repository']}:{i['Tag']}", "size": i["Size"]}
            for i in images
            if i["Repository"]
            in (
                "cob-rag",
                "ghcr.io/huggingface/text-embeddings-inference",
                "qdrant/qdrant",
                "postgres",
                "redis",
                "nginx",
            )
            or i["Repository"].startswith(("cob-rag", "cgr.dev"))
        ],
    }


def assistant_overhead(api: str, admin: CobRag, n: int = 20) -> dict[str, Any]:
    """p50/p95 end-to-end /query/stream time for the same questions with the assistant on and
    off (needs an LLM). Bar (C1): p95(on) - p95(off) <= 300 ms for business questions.

    Both arms answer through the same knowledge base and LLM; only `assistant.enabled`
    differs, so the difference is the director's own cost (rules, the label call running in
    parallel with retrieval, the fact check per sentence, the writes before `done`)."""
    from documents import FILES, QUESTIONS

    corpus = admin.create_corpus(f"overhead-{int(time.time())}", tenant_id=bench_tenant(admin))
    rag = CobRag(api, api_key=corpus["api_key"], timeout=900)
    key = corpus["api_key"]
    try:
        for path in sorted(FILES.iterdir())[:3]:
            rag.ingest(path, mode="new", wait=True, poll_interval=0.5, wait_timeout=900)
        questions = [json.loads(x)["q"] for x in QUESTIONS.read_text("utf-8").splitlines() if x][:n]

        def timed(enabled: bool) -> list[float]:
            httpx.post(
                f"{api}/api/v1/corpora/{corpus['id']}/assistant/enabled",
                headers={"X-API-Key": admin_key()},
                json={"enabled": enabled},
                timeout=30,
            ).raise_for_status()
            out = []
            for q in questions:
                t = time.perf_counter()
                with httpx.stream(
                    "POST",
                    f"{api}/api/v1/query/stream",
                    json={"query": q},
                    headers={"X-API-Key": key},
                    timeout=120,
                ) as res:
                    for _ in res.iter_lines():
                        pass
                out.append((time.perf_counter() - t) * 1000)
            return out

        off, on = timed(False), timed(True)
    finally:
        admin.delete_corpus(corpus["id"], corpus["name"])

    result = {
        "p50_off": round(statistics.median(off)),
        "p50_on": round(statistics.median(on)),
        "p95_off": round(percentile(off, 95)),
        "p95_on": round(percentile(on, 95)),
        "n": len(questions),
    }
    result["p95_added_ms"] = result["p95_on"] - result["p95_off"]
    result["passed"] = result["p95_added_ms"] <= 300
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--label", default="local")
    parser.add_argument("--seconds", type=float, default=20.0, help="per throughput level")
    parser.add_argument("--skip-load", action="store_true", help="skip latency/throughput")
    parser.add_argument(
        "--rerank-queries", type=int, default=200, help="queries timed with the reranker on"
    )
    parser.add_argument(
        "--assistant-overhead",
        action="store_true",
        help="only measure what the conversation director adds to an answer (needs an LLM)",
    )
    args = parser.parse_args()

    admin = CobRag(args.api, api_key=admin_key(), timeout=300)
    if args.assistant_overhead:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        overhead = assistant_overhead(args.api, admin)
        out = HERE / "results" / f"assistant-overhead-{stamp}-{args.label}"
        out.with_suffix(".json").write_text(
            json.dumps({"date": stamp, "label": args.label, **overhead}, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        verdict = "PASS" if overhead["passed"] else "FAIL"
        lines = [
            f"# Assistant overhead — {stamp} ({args.label})",
            "",
            f"{overhead['n']} questions through /query/stream, assistant off then on. **{verdict}**",
            "",
            "| | p50 | p95 |",
            "|---|---|---|",
            f"| Assistant off | {overhead['p50_off']} ms | {overhead['p95_off']} ms |",
            f"| Assistant on | {overhead['p50_on']} ms | {overhead['p95_on']} ms |",
            f"| Added | {overhead['p50_on'] - overhead['p50_off']} ms "
            f"| {overhead['p95_added_ms']} ms (bar: at most 300 ms) |",
        ]
        out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print("\n".join(lines))
        sys.exit(0 if overhead["passed"] else 1)
    report: dict[str, Any] = {"date": datetime.now(UTC).strftime("%Y-%m-%d"), "label": args.label}
    report["isolation"] = isolation(args.api, admin)
    report["widget"] = widget_abuse(args.api, admin)
    report["auth_overhead"] = auth_overhead(args.api, admin)
    if not args.skip_load:
        report["load"] = latency_and_throughput(args.api, admin, args.seconds, args.rerank_queries)
    report["footprint"] = footprint()

    iso_run = [r for r in report["isolation"] if r["refused"] is not None]
    iso_ok = sum(r["refused"] for r in iso_run)
    wid_ok = sum(r["ok"] for r in report["widget"])
    lines = [
        f"# Operations benchmark — {report['date']} ({args.label})",
        "",
        f"**Tenant isolation and account attacks: {iso_ok}/{len(iso_run)} attempts refused"
        " (checks held).**",
        f"**Widget abuse: {wid_ok}/{len(report['widget'])} checks passed.**",
        "",
        "| Caller | Attempt | Status | Refused |",
        "|---|---|---|---|",
        *[
            f"| {r['caller']} | `{r['attempt']}` | {r['status']} | "
            f"{'skipped' if r['refused'] is None else 'yes' if r['refused'] else '**NO**'} |"
            for r in report["isolation"]
        ],
        "",
        "| Widget check | Status | OK |",
        "|---|---|---|",
        *[
            f"| {r['check']} | {r['status']} | {'yes' if r['ok'] else '**NO**'} |"
            for r in report["widget"]
        ],
    ]
    auth = report["auth_overhead"]
    lines += ["", "## Auth overhead — GET /api/v1/whoami, one at a time", ""]
    if "skipped" in auth:
        lines.append(f"Skipped: {auth['skipped']}.")
    else:
        lines.append(
            f"Corpus key (cached): p50 {auth['corpus_key_ms']['p50']} ms, "
            f"p95 {auth['corpus_key_ms']['p95']} ms. Bearer token: "
            f"p50 {auth['bearer_ms']['p50']} ms, p95 {auth['bearer_ms']['p95']} ms "
            f"({auth['n']} requests each)."
        )
    if "load" in report:
        load = report["load"]
        lines += [
            "",
            f"## Retrieval latency — {load['corpus']}, {load['settings']}",
            "",
            f"One at a time: p50 {load['latency_ms']['p50']} ms, p95 {load['latency_ms']['p95']} ms, "
            f"p99 {load['latency_ms']['p99']} ms over {load['latency_ms']['n']} queries. "
            f"With the reranker on: p50 {load['latency_ms_with_reranker']['p50']} ms, "
            f"p95 {load['latency_ms_with_reranker']['p95']} ms.",
            "",
            "| Concurrent requests | Queries / second | p95 |",
            "|---|---|---|",
            *[f"| {t['concurrent']} | {t['qps']} | {t['p95_ms']} ms |" for t in load["throughput"]],
        ]
    lines += [
        "",
        "## Footprint",
        "",
        "| Container | Memory |",
        "|---|---|",
        *[f"| {c['name']} | {c['memory']} |" for c in report["footprint"]["containers"]],
    ]
    out = HERE / "results" / f"ops-{report['date']}-{args.label}"
    out.with_suffix(".json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
