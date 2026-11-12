"""cob-rag-client against a mocked engine (httpx.MockTransport)."""

import json

import httpx
import pytest
from cob_rag_client import AsyncCobRag, CobRag, CobRagError

calls: list[httpx.Request] = []
all_calls: list[httpx.Request] = []  # never cleared: the strictness check at the end reads it

SOURCES = {"query_id": "q1", "sources": [{"index": 1, "file_name": "returns.pdf"}]}
STREAM = (
    f"event: sources\ndata: {json.dumps(SOURCES)}\n\n"
    'event: token\ndata: {"content": "Hel"}\n\n'
    'event: token\ndata: {"content": "lo"}\n\n'
    'event: done\ndata: {"query_id": "q1", "model": "m", "usage": {"total_tokens": 3}}\n\n'
)
ASSISTANT_STREAM = (
    'event: stage\ndata: {"stage": "understand", "elapsed_ms": 1}\n\n'
    'event: meta\ndata: {"query_id": "q2", "conversation_id": null, "session": null, '
    '"label": "business_question", "route": "grounded", "language": "hi-Latn", '
    '"script": "latin", "assistant_version": 3}\n\n'
    'event: stage\ndata: {"stage": "search", "elapsed_ms": 40}\n\n'
    f"event: sources\ndata: {json.dumps(SOURCES)}\n\n"
    'event: token\ndata: {"content": "Rs 150 "}\n\n'
    'event: token\ndata: {"content": "lagenge."}\n\n'
    'event: quick_replies\ndata: {"items": [{"label": "Book a test", "message": "Book a test"}]}\n\n'
    'event: ui\ndata: {"kind": "lead_form", "form_id": "lf_0123456789abcdef"}\n\n'
    'event: done\ndata: {"query_id": "q2", "label": "business_question", "route": "grounded", '
    '"gate_threshold": 0.5, "timings": {"search": 40, "write": 300}}\n\n'
)
STREAM_FAIL = (
    f"event: sources\ndata: {json.dumps(SOURCES)}\n\n"
    'event: token\ndata: {"content": "Hel"}\n\n'
    'event: error\ndata: {"detail": "Generation failed part-way"}\n\n'
)


def handler(request: httpx.Request) -> httpx.Response:
    calls.append(request)
    all_calls.append(request)
    path = request.url.path
    if request.headers.get("X-API-Key") == "ck_read" and request.method == "POST":
        return httpx.Response(
            403,
            json={
                "detail": "This API key has read permission; this needs write",
                "code": "key_permission_denied",
                "permission": "read",
                "required": "write",
            },
        )
    if path == "/api/v1/query" and request.method == "POST":
        body = json.loads(request.content)
        if body["query"] == "capped":  # the monthly question cap (accounts A6)
            return httpx.Response(
                429,
                json={
                    "detail": "Monthly question limit reached — contact support@shree.example",
                    "code": "question_cap_reached",
                    "limit": 2,
                    "used": 2,
                    "resets_at": "2026-10-01T00:00:00Z",
                },
                headers={"Retry-After": "3600"},
            )
        return httpx.Response(200, json={"answer": f"echo:{body['query']}", "citations": []})
    if path == "/api/v1/admin/plans" and request.method == "GET":
        return httpx.Response(200, json={"items": [{"id": "p1"}], "count": 1})
    if path == "/api/v1/query/stream":
        body = json.loads(request.content)
        text = STREAM_FAIL if body["query"] == "fail" else STREAM
        if body.get("assistant"):
            text = ASSISTANT_STREAM
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
    if path == "/api/v1/ingest":
        return httpx.Response(202, json={"job_id": "j1", "status": "queued"})
    if path == "/api/v1/ingest/records":
        return httpx.Response(202, json={"job_id": "j1", "status": "queued", "records": 1})
    if path == "/api/v1/ingest/jobs/j1":
        done = sum(1 for c in calls if c.url.path == path) >= 2
        return httpx.Response(
            200, json={"id": "j1", "status": "completed" if done else "processing"}
        )
    if path.endswith("/widget-keys") and request.method == "POST":
        return httpx.Response(201, json={"public_key": "pk_x", **json.loads(request.content)})
    if path == "/api/v1/usage":
        return httpx.Response(200, json={"params": dict(request.url.params)})
    if path == "/api/v1/corpora" and request.method == "POST":
        body = json.loads(request.content)
        if "tenant_id" not in body:  # the admin key must name the tenant (accounts A1)
            return httpx.Response(
                422,
                json={
                    "detail": "tenant_id is required: every knowledge base belongs to a tenant",
                    "code": "tenant_required",
                },
            )
        return httpx.Response(201, json={"id": "c9", "api_key": "ck_new", **body})
    if path in ("/api/v1/corpora", "/api/v1/admin/tenants") and request.method == "GET":
        return httpx.Response(200, json={"items": [{"id": "c1"}], "count": 1})
    if path == "/api/v1/corpora/missing":
        return httpx.Response(404, json={"detail": "Corpus not found"})
    if path == "/api/v1/leads/export.csv":
        return httpx.Response(
            200, text="\ufeffcreated_at,knowledge_base\n", headers={"content-type": "text/csv"}
        )
    if request.method == "DELETE" and request.url.params.get("confirm_name") == "wrong":
        return httpx.Response(
            422,
            json={"detail": "confirm_name does not match", "code": "confirm_mismatch"},
        )
    if (
        request.method == "DELETE"
        and "confirm_name" not in request.url.params
        and (
            path.startswith(("/api/v1/corpora/c1", "/api/v1/documents/"))
            and not path.endswith(("/keys/k1", "/widget-keys/k1", "/whatsapp"))
        )
    ):
        return httpx.Response(
            422,
            json={
                "detail": "Deleting needs confirm_name",
                "code": "confirmation_required",
                "confirm_name": "Support FAQ",
            },
        )
    if path.endswith("/eval/runs") and request.method == "POST":
        return httpx.Response(202, json={"id": "r1", **json.loads(request.content)})
    if request.method == "GET" and path.endswith(
        ("/golden-sets", "/eval/criteria", "/eval/runs", "/assistant/review")
    ):
        return httpx.Response(200, json={"items": [{"id": "x"}], "count": 1})
    if path.endswith("/keys") and request.method == "GET":
        return httpx.Response(200, json={"keys": [{"id": "k1", "permission": "read"}]})
    if path.endswith("/keys") and request.method == "POST":
        body = json.loads(request.content)
        return httpx.Response(201, json={"id": "k2", "api_key": "ck_abcd_new", **body})
    if request.method == "DELETE":
        return httpx.Response(204)
    return httpx.Response(200, json={"path": path})


@pytest.fixture(autouse=True)
def _reset() -> None:
    calls.clear()


def client() -> CobRag:
    return CobRag("http://engine/", api_key="ck_test", transport=httpx.MockTransport(handler))


def test_sends_api_key_and_query_body() -> None:
    result = client().query(
        "refunds?",
        corpus_id="c1",
        options={"final_k": 3},
        llm={"provider": "openai", "model": "gpt-4o-mini", "api_key": "sk-x"},
    )
    assert result["answer"] == "echo:refunds?"
    req = calls[0]
    assert req.headers["x-api-key"] == "ck_test"
    assert json.loads(req.content) == {
        "query": "refunds?",
        "corpus_id": "c1",
        "options": {"final_k": 3},
    }
    assert req.headers["x-llm-provider"] == "openai"
    assert req.headers["x-llm-model"] == "gpt-4o-mini"
    assert req.headers["x-llm-api-key"] == "sk-x"


def test_stream_yields_tokens_until_done() -> None:
    assert list(client().query_stream("hi")) == ["Hel", "lo"]


def test_stream_events_carry_sources_and_done() -> None:
    events = list(client().query_stream_events("hi"))
    assert [e["event"] for e in events] == ["sources", "token", "token", "done"]
    assert events[0]["data"] == SOURCES
    assert events[-1]["data"]["query_id"] == "q1"


def test_stream_skips_stage_events_and_events_it_does_not_know() -> None:
    stage = 'event: stage\ndata: {"stage": "search", "elapsed_ms": 40}\n\n'
    text = stage + STREAM.replace("event: done", "event: future_thing\ndata: {}\n\nevent: done")
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
    )
    c = CobRag("http://engine", api_key="ck_t", transport=transport)
    assert list(c.query_stream("hi")) == ["Hel", "lo"]
    names = [e["event"] for e in c.query_stream_events("hi")]
    assert names == ["stage", "sources", "token", "token", "future_thing", "done"]


def test_stream_error_event_raises() -> None:
    stream = client().query_stream("fail")
    assert next(stream) == "Hel"
    with pytest.raises(CobRagError) as err:
        next(stream)
    assert err.value.detail == "Generation failed part-way"


def test_ingest_uploads_bytes_and_waits(tmp_path) -> None:
    job = client().ingest(b"# Returns\n30 days.", filename="returns.md", wait=True, poll_interval=0)
    assert job["status"] == "completed"
    upload = calls[0]
    assert b'filename="returns.md"' in upload.content
    assert b"30 days." in upload.content


def test_ingest_from_path(tmp_path) -> None:
    doc = tmp_path / "guide.txt"
    doc.write_text("hello")
    assert client().ingest(doc, corpus_id="c1")["job_id"] == "j1"
    assert b'name="corpus_id"' in calls[0].content


def test_create_corpus_names_its_tenant() -> None:
    created = client().create_corpus("Help", tenant_id="t1", description="d")
    assert json.loads(calls[0].content) == {"name": "Help", "tenant_id": "t1", "description": "d"}
    assert created["tenant_id"] == "t1"


def test_create_corpus_without_tenant_surfaces_tenant_required() -> None:
    with pytest.raises(CobRagError) as err:
        client().create_corpus("Help")
    assert "tenant_id" not in json.loads(calls[0].content)
    assert err.value.status == 422
    assert "tenant" in str(err.value.detail)


def test_widget_key_and_usage() -> None:
    c = client()
    key = c.create_widget_key("c1", "Site", ["https://acme.com"], rate_limit_per_minute=5)
    assert key["public_key"] == "pk_x" and key["rate_limit_per_minute"] == 5
    assert c.usage(group_by="source", start="2026-09-01", corpus_id=None)["params"] == {
        "group_by": "source",
        "start": "2026-09-01",
    }


def test_errors_carry_status_and_detail() -> None:
    with pytest.raises(CobRagError) as err:
        client().get_corpus("missing")
    assert err.value.status == 404 and err.value.detail == "Corpus not found"


def test_list_endpoints_return_the_items() -> None:
    assert client().list_corpora() == [{"id": "c1"}]
    assert client().list_tenants() == [{"id": "c1"}]


def test_list_corpora_pages_and_filters() -> None:
    client().list_corpora(offset=100, limit=50, tenant_id="t1")
    assert dict(calls[-1].url.params) == {"offset": "100", "limit": "50", "tenant_id": "t1"}


def test_delete_returns_none() -> None:
    assert client().delete_corpus("c1", "Support FAQ") is None
    assert dict(calls[-1].url.params) == {"confirm_name": "Support FAQ"}


def test_deletes_with_a_key_need_the_name_and_the_error_has_a_code() -> None:
    c = client()
    with pytest.raises(CobRagError) as err:
        c.delete_corpus("c1")
    assert err.value.status == 422 and err.value.code == "confirmation_required"
    with pytest.raises(CobRagError) as err:
        c.delete_document("d1", corpus_id="c1")
    assert err.value.code == "confirmation_required"
    with pytest.raises(CobRagError) as err:
        c.delete_document("d1", "wrong", corpus_id="c1")
    assert err.value.code == "confirm_mismatch"
    c.delete_document("d1", "refund-policy.pdf", corpus_id="c1")
    assert dict(calls[-1].url.params) == {"corpus_id": "c1", "confirm_name": "refund-policy.pdf"}
    assert calls[-1].url.path == "/api/v1/documents/d1"


def test_errors_without_a_code_have_none() -> None:
    with pytest.raises(CobRagError) as err:
        client().get_corpus("missing")
    assert err.value.code is None


def test_delete_previews() -> None:
    c = client()
    assert c.corpus_delete_preview("c1") == {"path": "/api/v1/corpora/c1/delete-preview"}
    c.document_delete_preview("d1", corpus_id="c1")
    assert calls[-1].url.path == "/api/v1/documents/d1/delete-preview"
    assert dict(calls[-1].url.params) == {"corpus_id": "c1"}
    c.tenant_delete_preview("t1")
    assert calls[-1].url.path == "/api/v1/admin/tenants/t1/delete-preview"


def test_a_permission_refusal_carries_its_code() -> None:
    c = CobRag("http://engine/", api_key="ck_read", transport=httpx.MockTransport(handler))
    with pytest.raises(CobRagError) as err:
        c.ingest_records([{"id": "a", "text": "x"}])
    assert err.value.status == 403 and err.value.code == "key_permission_denied"


def test_named_key_calls() -> None:
    c = client()
    assert c.list_corpus_keys("c1") == [{"id": "k1", "permission": "read"}]
    made = c.create_corpus_key("c1", "Agent - Cursor")
    assert made["api_key"] == "ck_abcd_new"
    assert json.loads(calls[-1].content) == {"name": "Agent - Cursor", "permission": "read"}
    c.create_corpus_key("c1", "Writer", "write")
    assert json.loads(calls[-1].content)["permission"] == "write"
    c.update_corpus_key("c1", "k1", permission="full")
    assert calls[-1].method == "PATCH" and calls[-1].url.path == "/api/v1/corpora/c1/keys/k1"
    assert json.loads(calls[-1].content) == {"permission": "full"}
    c.rotate_corpus_named_key("c1", "k1")
    assert calls[-1].url.path == "/api/v1/corpora/c1/keys/k1/rotate"
    assert c.revoke_corpus_key("c1", "k1") is None
    assert calls[-1].method == "DELETE" and calls[-1].url.path == "/api/v1/corpora/c1/keys/k1"

    assert c.list_my_keys()[0]["id"] == "k1"
    c.create_my_key("n8n", "write")
    c.update_my_key("k1", name="Renamed")
    c.rotate_my_named_key("k1")
    c.revoke_my_key("k1")
    assert [x.url.path for x in calls[-5:]] == [
        "/api/v1/tenants/me/keys",
        "/api/v1/tenants/me/keys",
        "/api/v1/tenants/me/keys/k1",
        "/api/v1/tenants/me/keys/k1/rotate",
        "/api/v1/tenants/me/keys/k1",
    ]

    assert c.list_tenant_keys("t1")[0]["id"] == "k1"
    c.create_tenant_key("t1", "Ops", "full")
    c.update_tenant_key("t1", "k1", name="Ops 2", permission="write")
    assert json.loads(calls[-1].content) == {"name": "Ops 2", "permission": "write"}
    c.rotate_tenant_named_key("t1", "k1")
    c.revoke_tenant_key("t1", "k1")
    assert calls[-1].url.path == "/api/v1/admin/tenants/t1/keys/k1"


async def test_async_deletes_previews_and_keys() -> None:
    c = AsyncCobRag("http://engine/", api_key="ck_test", transport=httpx.MockTransport(handler))
    with pytest.raises(CobRagError) as err:
        await c.delete_corpus("c1")
    assert err.value.code == "confirmation_required"
    await c.delete_corpus("c1", "Support FAQ")
    await c.delete_document("d1", "refund-policy.pdf", corpus_id="c1")
    assert dict(calls[-1].url.params) == {"corpus_id": "c1", "confirm_name": "refund-policy.pdf"}
    await c.corpus_delete_preview("c1")
    await c.document_delete_preview("d1", corpus_id="c1")
    await c.tenant_delete_preview("t1")
    assert await c.list_corpus_keys("c1") == [{"id": "k1", "permission": "read"}]
    assert (await c.create_corpus_key("c1", "Agent"))["permission"] == "read"
    await c.update_corpus_key("c1", "k1", name="x")
    await c.rotate_corpus_named_key("c1", "k1")
    await c.revoke_corpus_key("c1", "k1")
    assert (await c.list_my_keys())[0]["id"] == "k1"
    await c.create_my_key("n8n")
    await c.update_my_key("k1", permission="write")
    await c.rotate_my_named_key("k1")
    await c.revoke_my_key("k1")
    assert (await c.list_tenant_keys("t1"))[0]["id"] == "k1"
    await c.create_tenant_key("t1", "Ops")
    await c.update_tenant_key("t1", "k1", name="y")
    await c.rotate_tenant_named_key("t1", "k1")
    await c.revoke_tenant_key("t1", "k1")
    await c.aclose()


def test_tenant_lifecycle_calls() -> None:
    c = client()
    c.list_tenants(status="pending", search="shree", offset=100, limit=50)
    assert dict(calls[-1].url.params) == {
        "status": "pending",
        "search": "shree",
        "offset": "100",
        "limit": "50",
    }
    c.activate_tenant("t1", note="paid via UPI")
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/admin/tenants/t1/activate")
    assert json.loads(calls[-1].content) == {"note": "paid via UPI"}
    c.suspend_tenant("t1", "Shree Labs", reason="unpaid")
    assert calls[-1].url.path == "/api/v1/admin/tenants/t1/suspend"
    assert json.loads(calls[-1].content) == {"confirm_name": "Shree Labs", "reason": "unpaid"}
    c.reactivate_tenant("t1")
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/admin/tenants/t1/reactivate")
    assert c.decline_tenant("t1") is None
    assert calls[-1].url.path == "/api/v1/admin/tenants/t1/decline"
    assert json.loads(calls[-1].content) == {}
    assert c.delete_tenant("t1", "Shree Labs") is None
    assert calls[-1].method == "DELETE" and calls[-1].url.path == "/api/v1/admin/tenants/t1"
    assert dict(calls[-1].url.params) == {"confirm_name": "Shree Labs"}
    c.list_tenants(is_operator=False)
    assert dict(calls[-1].url.params)["is_operator"] == "false"
    c.delete_tenant("t0", "My workspace", confirm_operator_workspace=True)
    assert dict(calls[-1].url.params) == {
        "confirm_name": "My workspace",
        "confirm_operator_workspace": "true",
    }


async def test_async_tenant_lifecycle_calls() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="adm", transport=httpx.MockTransport(handler)
    ) as c:
        await c.list_tenants(status="suspended")
        assert dict(calls[-1].url.params) == {"status": "suspended", "offset": "0", "limit": "100"}
        await c.activate_tenant("t1")
        await c.suspend_tenant("t1", "Shree Labs")
        assert json.loads(calls[-1].content) == {"confirm_name": "Shree Labs"}
        await c.reactivate_tenant("t1")
        await c.decline_tenant("t1", reason="spam")
        await c.delete_tenant("t1", "Shree Labs")
    assert [r.url.path.rsplit("/", 1)[-1] for r in calls[1:]] == [
        "activate",
        "suspend",
        "reactivate",
        "decline",
        "t1",
    ]
    assert dict(calls[-1].url.params) == {"confirm_name": "Shree Labs"}


def test_plan_calls() -> None:
    c = client()
    assert c.list_plans() == [{"id": "p1"}]
    assert dict(calls[-1].url.params) == {"include_inactive": "true"}
    c.list_plans(include_inactive=False)
    assert dict(calls[-1].url.params) == {"include_inactive": "false"}
    c.create_plan("Starter", max_knowledge_bases=1, monthly_questions=1000)
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/admin/plans")
    assert json.loads(calls[-1].content) == {
        "name": "Starter",
        "max_knowledge_bases": 1,
        "monthly_questions": 1000,
    }
    c.update_plan("p1", monthly_questions=None)
    assert (calls[-1].method, calls[-1].url.path) == ("PATCH", "/api/v1/admin/plans/p1")
    assert json.loads(calls[-1].content) == {"monthly_questions": None}  # null = unlimited
    assert c.delete_plan("p1") is None
    assert (calls[-1].method, calls[-1].url.path) == ("DELETE", "/api/v1/admin/plans/p1")
    c.set_tenant_plan("t1", "p1")
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/admin/tenants/t1/plan")
    assert json.loads(calls[-1].content) == {"plan_id": "p1"}
    c.tenant_plan("t1")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/admin/tenants/t1/plan")
    c.my_plan()
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/tenants/me/plan")
    c.activate_tenant("t1", plan_id="p1", note="paid")
    assert json.loads(calls[-1].content) == {"plan_id": "p1", "note": "paid"}


async def test_async_plan_calls() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="adm", transport=httpx.MockTransport(handler)
    ) as c:
        assert await c.list_plans() == [{"id": "p1"}]
        await c.create_plan("Pro", max_documents=500)
        await c.update_plan("p1", is_active=False)
        await c.delete_plan("p1")
        await c.set_tenant_plan("t1", "p1")
        await c.tenant_plan("t1")
        await c.my_plan()
        await c.activate_tenant("t1", plan_id="p1")
        assert json.loads(calls[-1].content) == {"plan_id": "p1"}
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", "/api/v1/admin/plans"),
        ("POST", "/api/v1/admin/plans"),
        ("PATCH", "/api/v1/admin/plans/p1"),
        ("DELETE", "/api/v1/admin/plans/p1"),
        ("POST", "/api/v1/admin/tenants/t1/plan"),
        ("GET", "/api/v1/admin/tenants/t1/plan"),
        ("GET", "/api/v1/tenants/me/plan"),
        ("POST", "/api/v1/admin/tenants/t1/activate"),
    ]


def test_question_cap_is_a_plain_error_with_its_detail() -> None:
    with pytest.raises(CobRagError) as err:
        client().query("capped")
    assert err.value.status == 429
    assert "Monthly question limit reached" in str(err.value.detail)


def test_team_calls() -> None:
    c = client()
    c.team()
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/tenants/me/members")
    c.invite("ravi@example.com")
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/tenants/me/members")
    assert json.loads(calls[-1].content) == {"email": "ravi@example.com", "role": "member"}
    assert c.revoke_invitation("i1") is None
    assert (calls[-1].method, calls[-1].url.path) == ("DELETE", "/api/v1/tenants/me/invitations/i1")
    c.resend_invitation("i1")
    assert calls[-1].url.path == "/api/v1/tenants/me/invitations/i1/resend"
    c.set_member_role("u1", "owner")
    assert (calls[-1].method, calls[-1].url.path) == ("PATCH", "/api/v1/tenants/me/members/u1")
    assert json.loads(calls[-1].content) == {"role": "owner"}
    assert c.remove_member("u1") is None
    assert (calls[-1].method, calls[-1].url.path) == ("DELETE", "/api/v1/tenants/me/members/u1")
    c.tenant_team("t1")
    assert calls[-1].url.path == "/api/v1/admin/tenants/t1/members"
    c.invite_to_tenant("t1", "asha@example.com")
    assert (calls[-1].method, calls[-1].url.path) == (
        "POST",
        "/api/v1/admin/tenants/t1/invitations",
    )
    assert json.loads(calls[-1].content) == {"email": "asha@example.com", "role": "owner"}


async def test_async_team_calls() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_x", transport=httpx.MockTransport(handler)
    ) as c:
        await c.team()
        await c.invite("ravi@example.com", "owner")
        assert json.loads(calls[-1].content) == {"email": "ravi@example.com", "role": "owner"}
        await c.revoke_invitation("i1")
        await c.resend_invitation("i1")
        await c.set_member_role("u1", "member")
        await c.remove_member("u1")
        await c.tenant_team("t1")
        await c.invite_to_tenant("t1", "a@example.com", "member")
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", "/api/v1/tenants/me/members"),
        ("POST", "/api/v1/tenants/me/members"),
        ("DELETE", "/api/v1/tenants/me/invitations/i1"),
        ("POST", "/api/v1/tenants/me/invitations/i1/resend"),
        ("PATCH", "/api/v1/tenants/me/members/u1"),
        ("DELETE", "/api/v1/tenants/me/members/u1"),
        ("GET", "/api/v1/admin/tenants/t1/members"),
        ("POST", "/api/v1/admin/tenants/t1/invitations"),
    ]


async def test_async_client_query_and_stream() -> None:
    async with AsyncCobRag(
        "http://engine", api_key="tk_t", transport=httpx.MockTransport(handler)
    ) as c:
        assert (await c.query("x"))["answer"] == "echo:x"
        assert [t async for t in c.query_stream("x")] == ["Hel", "lo"]
        events = [e async for e in c.query_stream_events("x")]
        assert events[0] == {"event": "sources", "data": SOURCES}
        assert events[-1]["event"] == "done"
        job = await c.ingest(b"data", filename="a.md", wait=True, poll_interval=0)
        assert job["status"] == "completed"
    assert calls[0].headers["x-api-key"] == "tk_t"


def test_ingest_records_posts_json_and_waits() -> None:
    job = client().ingest_records(
        [{"id": "faq-1", "text": "30 days"}], corpus_id="c1", wait=True, poll_interval=0
    )
    assert job["status"] == "completed"
    assert json.loads(calls[0].content) == {
        "records": [{"id": "faq-1", "text": "30 days"}],
        "corpus_id": "c1",
    }


async def test_async_ingest_records() -> None:
    rag = AsyncCobRag("http://engine/", api_key="ck_test", transport=httpx.MockTransport(handler))
    job = await rag.ingest_records([{"id": "a", "text": "b"}], wait=True, poll_interval=0)
    assert job["status"] == "completed"


def test_dashboard_reads_send_their_params() -> None:
    c = client()
    for call_, path, params in [
        (lambda: c.whoami(), "/api/v1/whoami", {}),
        (lambda: c.info(), "/api/v1/info", {}),
        (lambda: c.own_tenant(), "/api/v1/tenants/me", {}),
        (
            lambda: c.list_jobs(status="failed", corpus_id="c1", limit=5),
            "/api/v1/ingest/jobs",
            {"status": "failed", "corpus_id": "c1", "limit": "5"},
        ),
        (
            lambda: c.list_documents(corpus_id="c1", search="price"),
            "/api/v1/documents",
            {"corpus_id": "c1", "search": "price"},
        ),
        (
            lambda: c.get_document("d1", corpus_id="c1", chunk_limit=10),
            "/api/v1/documents/d1",
            {"corpus_id": "c1", "chunk_limit": "10"},
        ),
    ]:
        call_()
        assert calls[-1].url.path == path
        assert dict(calls[-1].url.params) == params
    c.rotate_own_tenant_key()
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/tenants/me/rotate-key")


def test_retry_ingest_job() -> None:
    c = client()
    c.retry_ingest_job("j1")
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/ingest/jobs/j1/retry")


async def test_async_retry_ingest_job() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_x", transport=httpx.MockTransport(handler)
    ) as c:
        await c.retry_ingest_job("j2")
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/ingest/jobs/j2/retry")


async def test_async_dashboard_reads() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_x", transport=httpx.MockTransport(handler)
    ) as c:
        await c.info()
        await c.list_documents(corpus_id="c1", search=None)
    assert calls[-2].url.path == "/api/v1/info"
    assert dict(calls[-1].url.params) == {"corpus_id": "c1"}


def test_engine_settings_and_public_branding() -> None:
    c = client()
    c.get_settings()
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/admin/settings")
    c.update_settings(
        signup_mode="closed", branding={"product_name": "Sahayak AI", "logo_url": None}
    )
    assert (calls[-1].method, calls[-1].url.path) == ("PATCH", "/api/v1/admin/settings")
    assert json.loads(calls[-1].content) == {
        "signup_mode": "closed",
        "branding": {"product_name": "Sahayak AI", "logo_url": None},
    }
    c.public_branding()
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/public/branding")


async def test_async_engine_settings_and_public_branding() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="admin", transport=httpx.MockTransport(handler)
    ) as c:
        await c.get_settings()
        await c.update_settings(signup_mode="open")
        await c.public_branding()
    assert [(r.method, r.url.path) for r in calls[-3:]] == [
        ("GET", "/api/v1/admin/settings"),
        ("PATCH", "/api/v1/admin/settings"),
        ("GET", "/api/v1/public/branding"),
    ]
    assert json.loads(calls[-2].content) == {"signup_mode": "open"}


def test_smtp_status_and_test_email() -> None:
    c = client()
    c.smtp_status()
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/admin/smtp")
    c.send_test_email("me@example.com")
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/admin/smtp/test")
    assert json.loads(calls[-1].content) == {"to": "me@example.com"}


async def test_async_smtp_status_and_test_email() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="admin", transport=httpx.MockTransport(handler)
    ) as c:
        await c.smtp_status()
        await c.send_test_email("me@example.com")
    assert [(r.method, r.url.path) for r in calls[-2:]] == [
        ("GET", "/api/v1/admin/smtp"),
        ("POST", "/api/v1/admin/smtp/test"),
    ]
    assert json.loads(calls[-1].content) == {"to": "me@example.com"}


def test_audit_log_and_admin_team_calls() -> None:
    c = client()
    c.audit_log()
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/admin/audit")
    assert dict(calls[-1].url.params) == {"offset": "0", "limit": "50"}
    c.audit_log(tenant_id="t1", action="tenant.", actor="asha", start="2026-09-01", limit=10)
    assert dict(calls[-1].url.params) == {
        "tenant_id": "t1",
        "action": "tenant.",
        "actor": "asha",
        "start": "2026-09-01",
        "offset": "0",
        "limit": "10",
    }
    c.get_plan("p1")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/admin/plans/p1")
    assert c.revoke_tenant_invitation("t1", "i1") is None
    assert (calls[-1].method, calls[-1].url.path) == (
        "DELETE",
        "/api/v1/admin/tenants/t1/invitations/i1",
    )
    c.resend_tenant_invitation("t1", "i1")
    assert (calls[-1].method, calls[-1].url.path) == (
        "POST",
        "/api/v1/admin/tenants/t1/invitations/i1/resend",
    )
    c.set_tenant_member_role("t1", "u1", "owner")
    assert (calls[-1].method, calls[-1].url.path) == (
        "PATCH",
        "/api/v1/admin/tenants/t1/members/u1",
    )
    assert json.loads(calls[-1].content) == {"role": "owner"}
    assert c.remove_tenant_member("t1", "u1") is None
    assert (calls[-1].method, calls[-1].url.path) == (
        "DELETE",
        "/api/v1/admin/tenants/t1/members/u1",
    )


async def test_async_audit_log_and_admin_team_calls() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="admin", transport=httpx.MockTransport(handler)
    ) as c:
        await c.audit_log(action="member.invited", end="2026-09-30T00:00:00Z", offset=50)
        assert dict(calls[-1].url.params) == {
            "action": "member.invited",
            "end": "2026-09-30T00:00:00Z",
            "offset": "50",
            "limit": "50",
        }
        await c.get_plan("p1")
        await c.revoke_tenant_invitation("t1", "i1")
        await c.resend_tenant_invitation("t1", "i1")
        await c.set_tenant_member_role("t1", "u1", "member")
        await c.remove_tenant_member("t1", "u1")
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", "/api/v1/admin/audit"),
        ("GET", "/api/v1/admin/plans/p1"),
        ("DELETE", "/api/v1/admin/tenants/t1/invitations/i1"),
        ("POST", "/api/v1/admin/tenants/t1/invitations/i1/resend"),
        ("PATCH", "/api/v1/admin/tenants/t1/members/u1"),
        ("DELETE", "/api/v1/admin/tenants/t1/members/u1"),
    ]


def test_assistant_calls() -> None:
    c = client()
    c.get_assistant("c1")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/corpora/c1/assistant")
    c.put_assistant_draft("c1", {"voice": {"emoji": "light"}}, base_revision=2)
    assert json.loads(calls[-1].content) == {
        "config": {"voice": {"emoji": "light"}},
        "base_revision": 2,
    }
    c.put_assistant_draft("c1", {})
    assert json.loads(calls[-1].content) == {"config": {}}  # no base_revision when none given
    c.patch_assistant_draft("c1", {"identity": {"business_name": "Shree Labs"}})
    assert calls[-1].method == "PATCH"
    assert json.loads(calls[-1].content) == {"patch": {"identity": {"business_name": "Shree Labs"}}}
    c.assistant_draft_from_template("c1", "clinic", keep_identity=False)
    assert json.loads(calls[-1].content) == {"template": "clinic", "keep_identity": False}
    assert c.discard_assistant_draft("c2") is None
    assert (calls[-1].method, calls[-1].url.path) == (
        "DELETE",
        "/api/v1/corpora/c2/assistant/draft",
    )
    c.publish_assistant("c1", base_revision=3, note="Diwali hours")
    assert json.loads(calls[-1].content) == {"base_revision": 3, "note": "Diwali hours"}
    c.publish_assistant("c1", base_revision=0)
    assert json.loads(calls[-1].content) == {"base_revision": 0}
    c.assistant_versions("c1", limit=5)
    assert dict(calls[-1].url.params) == {"offset": "0", "limit": "5"}
    c.assistant_version("c1", 2)
    assert calls[-1].url.path == "/api/v1/corpora/c1/assistant/versions/2"
    c.restore_assistant_version("c1", 2)
    assert calls[-1].url.path == "/api/v1/corpora/c1/assistant/versions/2/restore"
    c.set_assistant_enabled("c1", False)
    assert json.loads(calls[-1].content) == {"enabled": False}
    c.assistant_analytics("c1", from_="2026-10-01T00:00:00Z")
    assert calls[-1].url.path == "/api/v1/corpora/c1/assistant/analytics"
    assert dict(calls[-1].url.params) == {"from": "2026-10-01T00:00:00Z"}
    c.assistant_analytics()
    assert calls[-1].url.path == "/api/v1/assistant/analytics"
    c.compile_assistant("c1", sample={"language": "hi-Latn"})
    assert json.loads(calls[-1].content) == {"sample": {"language": "hi-Latn"}}
    c.compile_assistant("c1")
    assert json.loads(calls[-1].content) == {}
    c.assistant_templates()
    assert calls[-1].url.path == "/api/v1/assistant/templates"
    c.assistant_template("clinic")
    assert calls[-1].url.path == "/api/v1/assistant/templates/clinic"


async def test_async_assistant_calls() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_x", transport=httpx.MockTransport(handler)
    ) as c:
        await c.get_assistant("c1")
        await c.put_assistant_draft("c1", {}, base_revision=1)
        await c.patch_assistant_draft("c1", {"voice": {"emoji": "light"}})
        await c.assistant_draft_from_template("c1", "store")
        assert await c.discard_assistant_draft("c2") is None
        await c.publish_assistant("c1", base_revision=1)
        await c.assistant_versions("c1")
        await c.assistant_version("c1", 1)
        await c.restore_assistant_version("c1", 1)
        await c.set_assistant_enabled("c1", True)
        await c.compile_assistant("c1", config={})
        await c.assistant_templates()
        await c.assistant_template("saas")
    assert [r.url.path.rsplit("/", 1)[-1] for r in calls[-13:]] == [
        "assistant",
        "draft",
        "draft",
        "from-template",
        "draft",
        "publish",
        "versions",
        "1",
        "restore",
        "enabled",
        "compile",
        "templates",
        "saas",
    ]
    assert json.loads(calls[3].content) == {"template": "store", "keep_identity": True}


# --- the engine refuses fields it does not declare (every request body is extra="forbid") -----


def _unknown_fields(spec: dict, schema: dict, value: object, path: str, out: list[str]) -> None:
    while "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    branches = schema.get("anyOf", []) + schema.get("oneOf", [])
    if branches:
        best: list[str] | None = None
        for branch in branches:
            found: list[str] = []
            _unknown_fields(spec, branch, value, path, found)
            if not found:
                return
            best = found if best is None or len(found) < len(best) else best
        out.extend(best or [])
        return
    for part in schema.get("allOf", []):
        _unknown_fields(spec, part, value, path, out)
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _unknown_fields(spec, schema["items"], item, f"{path}.{i}", out)
    elif isinstance(value, dict):
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                _unknown_fields(spec, props[key], item, f"{path}.{key}", out)
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}.{key}")


def test_assistant_options_are_sent() -> None:
    client().query("hi", assistant={"use_draft": True})
    assert json.loads(calls[-1].content)["assistant"] == {"use_draft": True}


def test_no_assistant_option_sends_no_field() -> None:
    client().query("hi")
    assert "assistant" not in json.loads(calls[-1].content)


def test_stream_ignores_assistant_events_for_tokens() -> None:
    assert list(client().query_stream("hi", assistant={"debug": True})) == ["Rs 150 ", "lagenge."]
    assert json.loads(calls[-1].content)["assistant"] == {"debug": True}


def test_stream_events_include_assistant_events_in_order() -> None:
    events = list(client().query_stream_events("hi", assistant={"debug": True}))
    assert [e["event"] for e in events] == [
        "stage",
        "meta",
        "stage",
        "sources",
        "token",
        "token",
        "quick_replies",
        "ui",
        "done",
    ]
    meta = next(e["data"] for e in events if e["event"] == "meta")
    assert meta["route"] == "grounded" and meta["language"] == "hi-Latn"
    assert events[-1]["data"]["gate_threshold"] == 0.5


async def test_async_assistant_options_and_events() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="ck_test", transport=httpx.MockTransport(handler)
    ) as c:
        await c.query("hi", assistant={"conversation_id": "c1"})
        assert json.loads(calls[-1].content)["assistant"] == {"conversation_id": "c1"}
        tokens = [t async for t in c.query_stream("hi", assistant={"debug": True})]
        assert tokens == ["Rs 150 ", "lagenge."]
        names = [e["event"] async for e in c.query_stream_events("hi", assistant={"debug": True})]
        assert names[1] == "meta" and names[-1] == "done"


def test_golden_set_and_criteria_calls() -> None:
    c = client()
    assert c.list_golden_sets("c1") == [{"id": "x"}]
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/corpora/c1/golden-sets")
    c.create_golden_set("c1", "Basics", [{"query": "Hi", "expected_behaviour": "smalltalk"}])
    assert json.loads(calls[-1].content) == {
        "name": "Basics",
        "pairs": [{"query": "Hi", "expected_behaviour": "smalltalk"}],
    }
    c.create_golden_set("c1", "Empty")
    assert json.loads(calls[-1].content) == {"name": "Empty", "pairs": []}
    c.golden_set_from_template("c1", "clinic")
    assert calls[-1].url.path == "/api/v1/corpora/c1/golden-sets/from-template"
    assert json.loads(calls[-1].content) == {"template": "clinic"}
    c.get_golden_set("c1", "s1")
    assert calls[-1].url.path == "/api/v1/corpora/c1/golden-sets/s1"
    c.rename_golden_set("c1", "s1", "Renamed")
    assert (calls[-1].method, json.loads(calls[-1].content)) == ("PATCH", {"name": "Renamed"})
    c.add_golden_pairs("c1", "s1", [{"query": "Fees?", "expected_answer": "150"}])
    assert calls[-1].url.path == "/api/v1/corpora/c1/golden-sets/s1/pairs"
    assert c.delete_golden_set("c2", "s1") is None
    assert (calls[-1].method, calls[-1].url.path) == (
        "DELETE",
        "/api/v1/corpora/c2/golden-sets/s1",
    )
    assert c.list_eval_criteria("c1") == [{"id": "x"}]
    c.create_eval_criterion("c1", "mentions-fee", "States the fee", 0.8)
    assert json.loads(calls[-1].content) == {
        "name": "mentions-fee",
        "definition": "States the fee",
        "threshold": 0.8,
    }
    c.update_eval_criterion("c1", "k1", enabled=False)
    assert (calls[-1].method, json.loads(calls[-1].content)) == ("PATCH", {"enabled": False})
    c.delete_eval_criterion("c2", "k1")
    assert calls[-1].url.path == "/api/v1/corpora/c2/eval/criteria/k1"
    c.get_eval_settings("c1")
    c.set_eval_settings("c1", judge_model="judge-x", thresholds={"answer_correctness": 0.8})
    assert (calls[-1].method, json.loads(calls[-1].content)) == (
        "PUT",
        {"judge_model": "judge-x", "thresholds": {"answer_correctness": 0.8}},
    )


def test_start_eval_run_sends_snapshot_and_confirmation() -> None:
    run = client().start_eval_run(
        "c1",
        "s1",
        mode="full",
        name="rerank on",
        settings={"assistant": "draft", "retrieval": {"reranking_enabled": True}},
        confirm_cost_usd=0.11,
    )
    req = calls[0]
    assert req.method == "POST" and req.url.path == "/api/v1/corpora/c1/eval/runs"
    assert json.loads(req.content) == {
        "set_id": "s1",
        "mode": "full",
        "name": "rerank on",
        "settings": {"assistant": "draft", "retrieval": {"reranking_enabled": True}},
        "confirm_cost_usd": 0.11,
    }
    assert run["id"] == "r1"
    client().start_eval_run("c1", "s1")
    assert json.loads(calls[1].content) == {"set_id": "s1", "mode": "fast"}  # nothing unset is sent


def test_eval_estimate_runs_samples_and_compare() -> None:
    c = client()
    c.estimate_eval_run("c1", "s1", settings={"assistant": 3})
    assert calls[-1].url.path == "/api/v1/corpora/c1/eval/estimate"
    assert json.loads(calls[-1].content) == {
        "set_id": "s1",
        "mode": "full",
        "settings": {"assistant": 3},
    }
    assert c.list_eval_runs("c1", set_id="s1", kind="golden") == [{"id": "x"}]
    assert dict(calls[-1].url.params) == {"set_id": "s1", "kind": "golden"}
    c.get_eval_run("c1", "r1")
    assert calls[-1].url.path == "/api/v1/corpora/c1/eval/runs/r1"
    c.list_eval_samples(
        "c1", "r1", failed_only=True, metric="answer_correctness", below=0.7, tag="difficulty:hard"
    )
    params = calls[-1].url.params
    assert calls[-1].url.path == "/api/v1/corpora/c1/eval/runs/r1/samples"
    assert params["failed_only"] == "true" and params["metric"] == "answer_correctness"
    assert params["below"] == "0.7" and params["tag"] == "difficulty:hard"
    c.list_eval_samples("c1", "r1")
    assert "failed_only" not in calls[-1].url.params and calls[-1].url.params["limit"] == "50"
    c.rate_eval_sample("c1", "m1", "poor")
    assert (calls[-1].method, json.loads(calls[-1].content)) == ("PATCH", {"rating": "poor"})
    c.rate_eval_sample("c1", "m1", None)
    assert json.loads(calls[-1].content) == {"rating": None}
    c.compare_eval_runs("c1", ["r1", "r2"])
    assert calls[-1].url.path == "/api/v1/corpora/c1/eval/compare"
    assert json.loads(calls[-1].content) == {"run_ids": ["r1", "r2"]}


def test_review_calls() -> None:
    c = client()
    assert c.review_items("c1", kind="no_answer", since="2026-09-01T00:00:00Z") == [{"id": "x"}]
    assert calls[-1].url.path == "/api/v1/corpora/c1/assistant/review"
    assert dict(calls[-1].url.params) == {"kind": "no_answer", "from": "2026-09-01T00:00:00Z"}
    c.review_action(
        "c1",
        "k" * 32,
        "added_answer",
        base_revision=0,
        answer={"topic_name": "MRI", "question": "MRI?", "answer": "No."},
    )
    assert calls[-1].url.path == "/api/v1/corpora/c1/assistant/review/actions"
    assert json.loads(calls[-1].content) == {
        "item_key": "k" * 32,
        "action": "added_answer",
        "base_revision": 0,
        "answer": {"topic_name": "MRI", "question": "MRI?", "answer": "No."},
    }


async def test_async_evaluation_calls() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_t", transport=httpx.MockTransport(handler)
    ) as c:
        assert await c.list_golden_sets("c1") == [{"id": "x"}]
        await c.create_golden_set("c1", "Basics")
        await c.start_eval_run("c1", "s1", mode="full", confirm_cost_usd=0.1)
        await c.list_eval_samples("c1", "r1", failed_only=True)
        await c.compare_eval_runs("c1", ["r1", "r2"])
        await c.review_items("c1")
        assert await c.delete_golden_set("c2", "s1") is None
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", "/api/v1/corpora/c1/golden-sets"),
        ("POST", "/api/v1/corpora/c1/golden-sets"),
        ("POST", "/api/v1/corpora/c1/eval/runs"),
        ("GET", "/api/v1/corpora/c1/eval/runs/r1/samples"),
        ("POST", "/api/v1/corpora/c1/eval/compare"),
        ("GET", "/api/v1/corpora/c1/assistant/review"),
        ("DELETE", "/api/v1/corpora/c2/golden-sets/s1"),
    ]


def test_guided_setup_calls() -> None:
    c = client()
    base = "/api/v1/corpora/c1/assistant/setup"
    c.start_setup("c1", business_type="clinic", website_url="https://shreelabs.in")
    assert (calls[-1].method, calls[-1].url.path) == ("POST", base)
    assert json.loads(calls[-1].content) == {
        "business_type": "clinic",
        "website_url": "https://shreelabs.in",
    }
    c.start_setup("c1")
    assert json.loads(calls[-1].content) == {}
    c.get_setup("c1", "s1")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", f"{base}/s1")
    pages = [{"url": "https://shreelabs.in/", "title": "Home", "text": "Blood tests"}]
    c.propose_setup("c1", "s1", pages, crawl_job_id="job-1")
    assert calls[-1].url.path == f"{base}/s1/propose"
    assert json.loads(calls[-1].content) == {"pages": pages, "crawl_job_id": "job-1"}
    c.setup_questions("c1", "s1")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", f"{base}/s1/questions")
    c.answer_setup("c1", "s1", {"tone": "warm"}, partial=True)
    assert json.loads(calls[-1].content) == {"answers": {"tone": "warm"}, "partial": True}
    c.answer_setup(
        "c1", "s1", {"tone": "warm"}, business_type="clinic", profile={"phone": "+919822012345"}
    )
    assert calls[-1].url.path == f"{base}/s1/answers"
    assert json.loads(calls[-1].content) == {
        "answers": {"tone": "warm"},
        "business_type": "clinic",
        "profile": {"phone": "+919822012345"},
    }
    c.generate_setup("c1", "s1", wording="template")
    assert json.loads(calls[-1].content) == {"wording": "template"}
    c.generate_setup("c1", "s1", base_revision=2)
    assert json.loads(calls[-1].content) == {"wording": "ai", "base_revision": 2}
    c.run_setup_tests("c1", "s1")
    assert (calls[-1].method, calls[-1].url.path) == ("POST", f"{base}/s1/tests")
    assert not calls[-1].content
    c.setup_gaps("c1", "s1")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", f"{base}/s1/gaps")
    c.apply_setup("c1", "s1", 3, note="Guided setup")
    assert calls[-1].url.path == f"{base}/s1/apply"
    assert json.loads(calls[-1].content) == {"base_revision": 3, "note": "Guided setup"}


async def test_async_guided_setup_calls() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_t", transport=httpx.MockTransport(handler)
    ) as c:
        await c.start_setup("c1", business_type="store")
        await c.get_setup("c1", "s1")
        await c.propose_setup("c1", "s1", [{"url": "https://x.in/", "title": "", "text": "x"}])
        await c.setup_questions("c1", "s1")
        await c.answer_setup("c1", "s1", {"tone": "warm"})
        await c.generate_setup("c1", "s1")
        await c.run_setup_tests("c1", "s1")
        await c.setup_gaps("c1", "s1")
        await c.apply_setup("c1", "s1", 1)
    base = "/api/v1/corpora/c1/assistant/setup"
    assert [(r.method, r.url.path) for r in calls] == [
        ("POST", base),
        ("GET", f"{base}/s1"),
        ("POST", f"{base}/s1/propose"),
        ("GET", f"{base}/s1/questions"),
        ("POST", f"{base}/s1/answers"),
        ("POST", f"{base}/s1/generate"),
        ("POST", f"{base}/s1/tests"),
        ("GET", f"{base}/s1/gaps"),
        ("POST", f"{base}/s1/apply"),
    ]


def test_leads_methods() -> None:
    c = client()
    c.list_leads(corpus_id="c1", status="new", q="9876543210")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/leads")
    assert dict(calls[-1].url.params) == {
        "corpus_id": "c1",
        "status": "new",
        "q": "9876543210",
        "offset": "0",
        "limit": "25",
    }
    c.get_lead("l1")
    assert calls[-1].url.path == "/api/v1/leads/l1"
    assert c.export_leads(corpus_id="c1", status="won").startswith("\ufeffcreated_at")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/leads/export.csv")
    assert dict(calls[-1].url.params) == {"corpus_id": "c1", "status": "won"}
    c.update_lead("l1", status="contacted", assignee_user_id=None)
    assert calls[-1].method == "PATCH"
    assert json.loads(calls[-1].content) == {"status": "contacted", "assignee_user_id": None}
    assert c.delete_lead("l1") is None
    assert (calls[-1].method, calls[-1].url.path) == ("DELETE", "/api/v1/leads/l1")
    c.withdraw_lead_consent("t" * 30)
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/public/leads/withdraw")
    assert json.loads(calls[-1].content) == {"token": "t" * 30}


def test_handoff_methods() -> None:
    c = client()
    c.list_handoffs(corpus_id="c1", status="new", date_from="2026-10-01", limit=10)
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/handoffs")
    assert dict(calls[-1].url.params) == {
        "corpus_id": "c1",
        "status": "new",
        "from": "2026-10-01",
        "offset": "0",
        "limit": "10",
    }
    c.get_handoff("h1")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/handoffs/h1")
    c.update_handoff("h1", "handled")
    assert (calls[-1].method, calls[-1].url.path) == ("PATCH", "/api/v1/handoffs/h1")
    assert json.loads(calls[-1].content) == {"status": "handled"}


async def test_async_handoff_methods() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_x", transport=httpx.MockTransport(handler)
    ) as c:
        await c.list_handoffs(corpus_id="c1")
        assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/handoffs")
        await c.get_handoff("h1")
        assert calls[-1].url.path == "/api/v1/handoffs/h1"
        await c.update_handoff("h1", "new")
        assert json.loads(calls[-1].content) == {"status": "new"}


def test_corpus_webhook_methods() -> None:
    c = client()
    c.create_corpus_webhook("c2", "https://crm.example.in/h", ["lead.created"], "s" * 20)
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/corpora/c2/webhooks")
    assert json.loads(calls[-1].content) == {
        "url": "https://crm.example.in/h",
        "events": ["lead.created"],
        "secret": "s" * 20,
    }
    c.update_corpus_webhook("c2", "w1", enabled=False)
    assert (calls[-1].method, calls[-1].url.path) == ("PATCH", "/api/v1/corpora/c2/webhooks/w1")
    assert json.loads(calls[-1].content) == {"enabled": False}
    assert c.delete_corpus_webhook("c2", "w1") is None
    assert calls[-1].url.path == "/api/v1/corpora/c2/webhooks/w1"


async def test_async_leads_methods() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_x", transport=httpx.MockTransport(handler)
    ) as c:
        await c.list_leads(corpus_id="c1")
        assert (await c.export_leads(corpus_id="c1")).startswith("\ufeffcreated_at")
        await c.get_lead("l1")
        await c.update_lead("l1", status="won")
        assert await c.delete_lead("l1") is None
        await c.withdraw_lead_consent("t" * 30)
        await c.create_corpus_webhook("c2", "https://crm.example.in/h", ["lead.created"], "s" * 20)
        await c.update_corpus_webhook("c2", "w1", enabled=True)
        assert await c.delete_corpus_webhook("c2", "w1") is None
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", "/api/v1/leads"),
        ("GET", "/api/v1/leads/export.csv"),
        ("GET", "/api/v1/leads/l1"),
        ("PATCH", "/api/v1/leads/l1"),
        ("DELETE", "/api/v1/leads/l1"),
        ("POST", "/api/v1/public/leads/withdraw"),
        ("POST", "/api/v1/corpora/c2/webhooks"),
        ("PATCH", "/api/v1/corpora/c2/webhooks/w1"),
        ("DELETE", "/api/v1/corpora/c2/webhooks/w1"),
    ]


async def test_async_list_corpus_webhooks_unwraps_the_list() -> None:
    def hooks(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"webhooks": [{"id": "w1"}], "count": 1})

    async with AsyncCobRag(
        "http://engine/", api_key="ck_x", transport=httpx.MockTransport(hooks)
    ) as c:
        assert await c.list_corpus_webhooks("c1") == [{"id": "w1"}]
    sync = CobRag("http://engine/", api_key="ck_x", transport=httpx.MockTransport(hooks))
    assert sync.list_corpus_webhooks("c1") == [{"id": "w1"}]


def test_export_errors_raise_with_a_code() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"detail": "Only a workspace owner can do this", "code": "owner_required"}
        )

    c = CobRag("http://engine/", api_key="tk_x", transport=httpx.MockTransport(refuse))
    with pytest.raises(CobRagError) as err:
        c.export_leads(corpus_id="c1")
    assert err.value.status == 403 and err.value.code == "owner_required"


def test_whatsapp_methods() -> None:
    c = client()
    c.whatsapp("c1")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/corpora/c1/whatsapp")
    c.set_whatsapp_manual(
        "c1",
        phone_number_id="1234567890",
        waba_id="5550001",
        access_token="EAAG-permanent-token",
        meta_billing="deployer",
    )
    assert (calls[-1].method, calls[-1].url.path) == ("PUT", "/api/v1/corpora/c1/whatsapp/manual")
    assert json.loads(calls[-1].content)["meta_billing"] == "deployer"
    c.update_whatsapp("c1", human_pause_hours=6)
    assert (calls[-1].method, json.loads(calls[-1].content)) == ("PATCH", {"human_pause_hours": 6})
    c.set_whatsapp_documents("c1", ["d1", "d2"])
    assert (calls[-1].method, calls[-1].url.path) == (
        "PUT",
        "/api/v1/corpora/c1/whatsapp/documents",
    )
    assert json.loads(calls[-1].content) == {"document_ids": ["d1", "d2"]}
    c.whatsapp_threads("c1", unread=True, limit=20)
    assert dict(calls[-1].url.params) == {"offset": "0", "limit": "20", "unread": "true"}
    c.whatsapp_thread("c1", "t1")
    assert calls[-1].url.path == "/api/v1/corpora/c1/whatsapp/threads/t1"
    c.whatsapp_reply("c1", "t1", "We open at 9.")
    assert (calls[-1].method, calls[-1].url.path) == (
        "POST",
        "/api/v1/corpora/c1/whatsapp/threads/t1/reply",
    )
    assert json.loads(calls[-1].content) == {"text": "We open at 9."}
    c.update_whatsapp_thread("c1", "t1", bot_paused=False)
    assert json.loads(calls[-1].content) == {"bot_paused": False}
    c.disconnect_whatsapp("c1")
    assert (calls[-1].method, calls[-1].url.path) == ("DELETE", "/api/v1/corpora/c1/whatsapp")


async def test_async_whatsapp_methods() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_x", transport=httpx.MockTransport(handler)
    ) as c:
        await c.whatsapp("c1")
        assert calls[-1].url.path == "/api/v1/corpora/c1/whatsapp"
        await c.whatsapp_reply("c1", "t1", "Hi")
        assert json.loads(calls[-1].content) == {"text": "Hi"}
        await c.update_whatsapp_thread("c1", "t1", bot_paused=True)
        assert json.loads(calls[-1].content) == {"bot_paused": True}
        await c.disconnect_whatsapp("c1")
        assert calls[-1].method == "DELETE"


def test_every_body_the_client_sent_only_has_fields_the_engine_declares() -> None:
    """Runs last in this file: checks the bodies of every call above against the committed
    OpenAPI spec, where a request model that refuses unknown fields has additionalProperties
    false. A method that sent a field the engine does not declare would 422 for real."""
    import re
    from pathlib import Path

    spec = json.loads(
        (Path(__file__).resolve().parents[3] / "docs" / "openapi.json").read_text("utf-8")
    )
    templates = [
        (re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$"), p, ops)
        for p, ops in spec["paths"].items()
    ]
    checked, problems = 0, []
    for req in all_calls:
        if not req.content or "json" not in req.headers.get("content-type", ""):
            continue
        matches = [
            (p.count("{"), ops)
            for rx, p, ops in templates
            if rx.match(req.url.path) and req.method.lower() in ops
        ]
        if not matches:
            continue
        ops = min(matches, key=lambda m: m[0])[1]
        schema = (
            ops[req.method.lower()]
            .get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema")
        )
        if schema is None:
            continue
        checked += 1
        found: list[str] = []
        _unknown_fields(spec, schema, json.loads(req.content), "body", found)
        problems += [f"{req.method} {req.url.path}: {f}" for f in found]
    assert checked > 10, "the check saw almost no bodies: is `all_calls` being filled?"
    assert not problems, problems


def test_restore_operator_workspace() -> None:
    c = client()
    c.restore_operator_workspace()
    assert (calls[-1].method, calls[-1].url.path) == (
        "POST",
        "/api/v1/admin/operator-workspace/restore",
    )


def test_improve_assistant_rule_posts_the_rule() -> None:
    client().improve_assistant_rule("c1", "greet hindi ppl in hindi", "communication")
    req = calls[-1]
    assert req.method == "POST" and req.url.path == "/api/v1/corpora/c1/assistant/rules/improve"
    assert json.loads(req.content) == {
        "instruction": "greet hindi ppl in hindi",
        "type": "communication",
    }


async def test_async_improve_assistant_rule_sends_rule_id() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_x", transport=httpx.MockTransport(handler)
    ) as c:
        await c.improve_assistant_rule("c1", "Be brief.", "communication", rule_id="tone-short")
    assert json.loads(calls[-1].content)["rule_id"] == "tone-short"


# BIL1 provider balances
def test_provider_balances_call() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"items": [], "worst": None})

    c = CobRag("http://engine/", api_key="adm", transport=httpx.MockTransport(answer))
    assert c.provider_balances() == {"items": [], "worst": None}
    assert (seen[0].method, seen[0].url.path) == ("GET", "/api/v1/admin/billing/providers")


async def test_async_provider_balances_call() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"items": [], "worst": None})

    async with AsyncCobRag(
        "http://engine/", api_key="adm", transport=httpx.MockTransport(answer)
    ) as c:
        assert (await c.provider_balances())["worst"] is None
    assert seen[0].url.path == "/api/v1/admin/billing/providers"


# /BIL1 provider balances


# BIL2 pricing and accounts
def _billing_client() -> tuple[CobRag, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True, "items": [], "total": 0})

    return CobRag("http://engine/", api_key="adm", transport=httpx.MockTransport(answer)), seen


def test_billing_pricing_and_accounts_calls() -> None:
    c, seen = _billing_client()
    c.get_plan_pricing("p1")
    c.set_plan_pricing("p1", usage_mode="per_question", per_question_minor=200)
    c.list_billing_accounts(q="shree", payment_type="postpaid", offset=25, limit=10)
    c.get_billing_account("t1")
    c.update_billing_account("t1", overrides={"per_question_minor": 150, "payment_type": None})
    c.billing_account()
    calls = [(r.method, r.url.path) for r in seen]
    assert calls == [
        ("GET", "/api/v1/admin/billing/plans/p1/pricing"),
        ("PUT", "/api/v1/admin/billing/plans/p1/pricing"),
        ("GET", "/api/v1/admin/billing/accounts"),
        ("GET", "/api/v1/admin/billing/accounts/t1"),
        ("PATCH", "/api/v1/admin/billing/accounts/t1"),
        ("GET", "/api/v1/billing/account"),
    ]
    assert json.loads(seen[1].content) == {"usage_mode": "per_question", "per_question_minor": 200}
    assert dict(seen[2].url.params) == {
        "q": "shree",
        "payment_type": "postpaid",
        "offset": "25",
        "limit": "10",
    }
    assert json.loads(seen[4].content) == {
        "overrides": {"per_question_minor": 150, "payment_type": None}
    }


async def test_async_billing_pricing_and_accounts_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    async with AsyncCobRag(
        "http://engine/", api_key="adm", transport=httpx.MockTransport(answer)
    ) as c:
        await c.get_plan_pricing("p1")
        await c.set_plan_pricing("p1", markup_bp=2500)
        await c.list_billing_accounts()
        await c.get_billing_account("t1")
        await c.update_billing_account("t1", notes="x")
        await c.billing_account()
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/admin/billing/plans/p1/pricing"),
        ("PUT", "/api/v1/admin/billing/plans/p1/pricing"),
        ("GET", "/api/v1/admin/billing/accounts"),
        ("GET", "/api/v1/admin/billing/accounts/t1"),
        ("PATCH", "/api/v1/admin/billing/accounts/t1"),
        ("GET", "/api/v1/billing/account"),
    ]
    assert json.loads(seen[1].content) == {"markup_bp": 2500}


# /BIL2 pricing and accounts


# BIL3 ledger
def test_ledger_calls() -> None:
    c, seen = _billing_client()
    c.list_ledger(
        "t1", entry_type="top_up", start="2026-10-01", end="2026-10-31", offset=50, limit=10
    )
    c.add_ledger_entry(
        "t1", entry_type="top_up", amount_minor=50_000, idempotency_key="abcdefgh12345678"
    )
    c.billing_ledger(limit=5)
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/admin/billing/accounts/t1/ledger"),
        ("POST", "/api/v1/admin/billing/accounts/t1/ledger"),
        ("GET", "/api/v1/billing/ledger"),
    ]
    assert dict(seen[0].url.params) == {
        "entry_type": "top_up",
        "start": "2026-10-01",
        "end": "2026-10-31",
        "offset": "50",
        "limit": "10",
    }
    assert json.loads(seen[1].content) == {
        "entry_type": "top_up",
        "amount_minor": 50_000,
        "idempotency_key": "abcdefgh12345678",
    }
    assert dict(seen[2].url.params) == {"offset": "0", "limit": "5"}


async def test_async_ledger_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"items": [], "total": 0})

    async with AsyncCobRag(
        "http://engine/", api_key="adm", transport=httpx.MockTransport(answer)
    ) as c:
        await c.list_ledger("t1")
        await c.add_ledger_entry("t1", entry_type="adjustment", amount_minor=-5, memo="x")
        await c.billing_ledger()
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/admin/billing/accounts/t1/ledger"),
        ("POST", "/api/v1/admin/billing/accounts/t1/ledger"),
        ("GET", "/api/v1/billing/ledger"),
    ]


# /BIL3 ledger


# BIL5 add-ons
def test_add_on_calls() -> None:
    c, seen = _billing_client()
    c.list_add_ons(include_inactive=True)
    c.create_add_on(
        kind="question_pack", name="500", currency="INR", price_minor=29_900, questions=500
    )
    c.update_add_on("a1", is_active=False)
    c.list_grants("t1", active_only=True)
    c.apply_add_on("t1", add_on_id="a1", idempotency_key="abcdefgh12345678", payment="external")
    c.revoke_grant("t1", "g1", memo="refunded")
    c.revoke_grant("t1", "g2")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/admin/billing/add-ons"),
        ("POST", "/api/v1/admin/billing/add-ons"),
        ("PATCH", "/api/v1/admin/billing/add-ons/a1"),
        ("GET", "/api/v1/admin/billing/accounts/t1/grants"),
        ("POST", "/api/v1/admin/billing/accounts/t1/grants"),
        ("POST", "/api/v1/admin/billing/accounts/t1/grants/g1/revoke"),
        ("POST", "/api/v1/admin/billing/accounts/t1/grants/g2/revoke"),
    ]
    assert dict(seen[0].url.params) == {"include_inactive": "true"}
    assert json.loads(seen[1].content)["questions"] == 500
    assert json.loads(seen[2].content) == {"is_active": False}
    assert dict(seen[3].url.params) == {"active_only": "true"}
    assert json.loads(seen[4].content) == {
        "add_on_id": "a1",
        "idempotency_key": "abcdefgh12345678",
        "payment": "external",
    }
    assert json.loads(seen[5].content) == {"memo": "refunded"}
    assert json.loads(seen[6].content) == {}


def test_delete_add_on_is_a_204() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(204)

    c = CobRag("http://engine/", api_key="adm", transport=httpx.MockTransport(answer))
    assert c.delete_add_on("a1") is None
    assert (seen[0].method, seen[0].url.path) == ("DELETE", "/api/v1/admin/billing/add-ons/a1")


async def test_async_add_on_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(204 if req.method == "DELETE" else 200, json={"items": []})

    async with AsyncCobRag(
        "http://engine/", api_key="adm", transport=httpx.MockTransport(answer)
    ) as c:
        await c.list_add_ons()
        await c.create_add_on(
            kind="question_pack", name="x", currency="INR", price_minor=0, questions=5
        )
        await c.update_add_on("a1", price_minor=1)
        await c.delete_add_on("a1")
        await c.list_grants("t1")
        await c.apply_add_on("t1", add_on_id="a1", idempotency_key="abcdefgh12345678")
        await c.revoke_grant("t1", "g1")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/admin/billing/add-ons"),
        ("POST", "/api/v1/admin/billing/add-ons"),
        ("PATCH", "/api/v1/admin/billing/add-ons/a1"),
        ("DELETE", "/api/v1/admin/billing/add-ons/a1"),
        ("GET", "/api/v1/admin/billing/accounts/t1/grants"),
        ("POST", "/api/v1/admin/billing/accounts/t1/grants"),
        ("POST", "/api/v1/admin/billing/accounts/t1/grants/g1/revoke"),
    ]


# /BIL5 add-ons


# BIL6 statements
def test_statement_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.params.get("format") == "csv":
            return httpx.Response(200, text="section,date\r\n")
        if req.url.params.get("format") == "html":
            return httpx.Response(200, text="<h1>Statement</h1>")
        return httpx.Response(200, json={"items": [], "sent": True, "month": "2026-10-01"})

    c = CobRag("http://engine/", api_key="adm", transport=httpx.MockTransport(answer))
    assert c.list_statements("t1") == {"items": [], "sent": True, "month": "2026-10-01"}
    assert c.get_statement("t1", "2026-10")["month"] == "2026-10-01"
    assert c.get_statement("t1", "2026-10", format="csv") == "section,date\r\n"
    assert c.get_statement("t1", "2026-10", format="html") == "<h1>Statement</h1>"
    c.billing_statements()
    assert c.billing_statement("2026-09")["month"] == "2026-10-01"
    assert c.billing_statement("2026-09", format="csv") == "section,date\r\n"
    assert c.request_top_up(amount_minor=500_000, note="UPI")["sent"] is True
    assert c.request_top_up()["sent"] is True
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/admin/billing/accounts/t1/statements"),
        ("GET", "/api/v1/admin/billing/accounts/t1/statements/2026-10"),
        ("GET", "/api/v1/admin/billing/accounts/t1/statements/2026-10"),
        ("GET", "/api/v1/admin/billing/accounts/t1/statements/2026-10"),
        ("GET", "/api/v1/billing/statements"),
        ("GET", "/api/v1/billing/statements/2026-09"),
        ("GET", "/api/v1/billing/statements/2026-09"),
        ("POST", "/api/v1/billing/top-up-requests"),
        ("POST", "/api/v1/billing/top-up-requests"),
    ]
    assert dict(seen[2].url.params) == {"format": "csv"}
    assert dict(seen[3].url.params) == {"format": "html"}
    assert dict(seen[1].url.params) == {}
    assert json.loads(seen[7].content) == {"amount_minor": 500_000, "note": "UPI"}
    assert json.loads(seen[8].content) == {}


def test_a_second_top_up_request_is_a_coded_429() -> None:
    def answer(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"detail": "too soon", "code": "topup_request_too_soon", "retry_after_s": 3600},
            headers={"Retry-After": "3600"},
        )

    c = CobRag("http://engine/", api_key="tk", transport=httpx.MockTransport(answer))
    with pytest.raises(CobRagError) as err:
        c.request_top_up(amount_minor=100)
    assert err.value.status == 429 and err.value.code == "topup_request_too_soon"


async def test_async_statement_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.params.get("format") == "csv":
            return httpx.Response(200, text="section,date\r\n")
        return httpx.Response(200, json={"items": [], "sent": True})

    async with AsyncCobRag(
        "http://engine/", api_key="adm", transport=httpx.MockTransport(answer)
    ) as c:
        await c.list_statements("t1")
        await c.get_statement("t1", "2026-10")
        assert await c.get_statement("t1", "2026-10", format="csv") == "section,date\r\n"
        await c.billing_statements()
        await c.billing_statement("2026-09")
        assert await c.billing_statement("2026-09", format="csv") == "section,date\r\n"
        await c.request_top_up(note="please")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/admin/billing/accounts/t1/statements"),
        ("GET", "/api/v1/admin/billing/accounts/t1/statements/2026-10"),
        ("GET", "/api/v1/admin/billing/accounts/t1/statements/2026-10"),
        ("GET", "/api/v1/billing/statements"),
        ("GET", "/api/v1/billing/statements/2026-09"),
        ("GET", "/api/v1/billing/statements/2026-09"),
        ("POST", "/api/v1/billing/top-up-requests"),
    ]
    assert json.loads(seen[6].content) == {"note": "please"}


# /BIL6 statements


# BIZ3 home and articles
def test_home_and_article_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(202 if req.method in ("POST", "PUT") else 200, json={"ok": True})

    c = CobRag("http://engine/", api_key="tk", transport=httpx.MockTransport(answer))
    assert c.home() == {"ok": True}
    c.home(corpus_id="kb1")
    c.create_article("kb1", "faq", "Do you open on Sunday?", "Yes, 10 am to 2 pm.")
    c.get_article("kb1", "d1")
    c.update_article("kb1", "d1", "faq", "Do you open on Sunday?", "Yes, 10 am to 1 pm.")
    c.list_documents(corpus_id="kb1", source="articles")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/home"),
        ("GET", "/api/v1/home"),
        ("POST", "/api/v1/corpora/kb1/articles"),
        ("GET", "/api/v1/corpora/kb1/articles/d1"),
        ("PUT", "/api/v1/corpora/kb1/articles/d1"),
        ("GET", "/api/v1/documents"),
    ]
    assert seen[1].url.params["corpus_id"] == "kb1"
    assert json.loads(seen[2].content) == {
        "kind": "faq",
        "title": "Do you open on Sunday?",
        "body": "Yes, 10 am to 2 pm.",
    }
    assert seen[5].url.params["source"] == "articles"


async def test_async_home_and_article_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    c = AsyncCobRag("http://engine/", api_key="tk", transport=httpx.MockTransport(answer))
    assert await c.home() == {"ok": True}
    await c.create_article("kb1", "article", "Root canal treatment", "From Rs 4,500.")
    await c.get_article("kb1", "d1")
    await c.update_article("kb1", "d1", "article", "Root canal", "From Rs 5,000.")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/home"),
        ("POST", "/api/v1/corpora/kb1/articles"),
        ("GET", "/api/v1/corpora/kb1/articles/d1"),
        ("PUT", "/api/v1/corpora/kb1/articles/d1"),
    ]


# /BIZ3 home and articles


# BIZ1 website learning
def test_website_learning_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(202 if req.method == "POST" else 200, json={"ok": True})

    c = CobRag("http://engine/", api_key="tk", transport=httpx.MockTransport(answer))
    assert c.website("kb1") == {"ok": True}
    c.update_website("kb1", enabled=False, exclude_patterns=["/blog/*", "/careers"])
    c.update_website("kb1", enabled=True)
    c.learn_website("kb1", "https://shreedental.in")
    c.reread_website("kb1")
    assert c.remove_website_source("kb1", "s1") is None
    c.website_pages("kb1", status="skipped", q="cart", limit=20)
    c.reread_website_page("kb1", "p1")
    c.update_website_page("kb1", "p1", True)
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/corpora/kb1/website"),
        ("PATCH", "/api/v1/corpora/kb1/website"),
        ("PATCH", "/api/v1/corpora/kb1/website"),
        ("POST", "/api/v1/corpora/kb1/website/learn"),
        ("POST", "/api/v1/corpora/kb1/website/reread"),
        ("DELETE", "/api/v1/corpora/kb1/website/sources/s1"),
        ("GET", "/api/v1/corpora/kb1/website/pages"),
        ("POST", "/api/v1/corpora/kb1/website/pages/p1/reread"),
        ("PATCH", "/api/v1/corpora/kb1/website/pages/p1"),
    ]
    assert json.loads(seen[1].content) == {
        "enabled": False,
        "exclude_patterns": ["/blog/*", "/careers"],
    }
    assert json.loads(seen[2].content) == {"enabled": True}  # unset fields are not sent
    assert json.loads(seen[3].content) == {"origin": "https://shreedental.in"}
    assert dict(seen[6].url.params) == {"status": "skipped", "q": "cart", "limit": "20"}
    assert json.loads(seen[8].content) == {"excluded": True}


def test_website_reread_cooldown_is_an_error_with_its_code() -> None:
    def answer(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "540"},
            json={"detail": "Wait.", "code": "website_reread_too_soon", "retry_after": 540},
        )

    c = CobRag("http://engine/", api_key="tk", transport=httpx.MockTransport(answer))
    with pytest.raises(CobRagError) as caught:
        c.reread_website("kb1")
    assert caught.value.status == 429 and caught.value.code == "website_reread_too_soon"


async def test_async_website_learning_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return (
            httpx.Response(204)
            if req.method == "DELETE"
            else httpx.Response(200, json={"ok": True})
        )

    c = AsyncCobRag("http://engine/", api_key="tk", transport=httpx.MockTransport(answer))
    assert await c.website("kb1") == {"ok": True}
    await c.update_website("kb1", exclude_patterns=[])
    await c.learn_website("kb1", "https://shreedental.in")
    await c.reread_website("kb1")
    assert await c.remove_website_source("kb1", "s1") is None
    await c.website_pages("kb1", offset=25)
    await c.reread_website_page("kb1", "p1")
    await c.update_website_page("kb1", "p1", False)
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/corpora/kb1/website"),
        ("PATCH", "/api/v1/corpora/kb1/website"),
        ("POST", "/api/v1/corpora/kb1/website/learn"),
        ("POST", "/api/v1/corpora/kb1/website/reread"),
        ("DELETE", "/api/v1/corpora/kb1/website/sources/s1"),
        ("GET", "/api/v1/corpora/kb1/website/pages"),
        ("POST", "/api/v1/corpora/kb1/website/pages/p1/reread"),
        ("PATCH", "/api/v1/corpora/kb1/website/pages/p1"),
    ]
    assert json.loads(seen[1].content) == {"exclude_patterns": []}


# /BIZ1 website learning


# BIZ2 freshness
def test_schedule_and_push_link_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "DELETE":
            return httpx.Response(204)
        if req.method == "POST":
            return httpx.Response(
                201,
                json={"key": "lk_x", "push_url": "https://e/api/v1/public/learn/lk_x", "hint": "x"},
            )
        return httpx.Response(200, json={"ok": True})

    c = CobRag("http://engine/", api_key="tk", transport=httpx.MockTransport(answer))
    c.update_website("kb1", refresh_mode="weekly", adaptive=False)
    made = c.create_learn_key("kb1")
    assert c.delete_learn_key("kb1") is None
    assert json.loads(seen[0].content) == {"refresh_mode": "weekly", "adaptive": False}
    assert made["key"] == "lk_x"
    assert [(r.method, r.url.path) for r in seen] == [
        ("PATCH", "/api/v1/corpora/kb1/website"),
        ("POST", "/api/v1/corpora/kb1/website/learn-key"),
        ("DELETE", "/api/v1/corpora/kb1/website/learn-key"),
    ]


# /BIZ2 freshness


# BIZ4 avatar
def test_upload_avatar_sends_the_picture_and_returns_the_engines_path() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            201,
            json={"avatar_url": "/api/v1/public/avatars/6f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b.png"},
        )

    c = CobRag("http://engine/", api_key="tk", transport=httpx.MockTransport(answer))
    out = c.upload_avatar("kb1", b"\x89PNG fake", filename="asha.png")
    assert out["avatar_url"].startswith("/api/v1/public/avatars/")
    assert (seen[0].method, seen[0].url.path) == ("POST", "/api/v1/corpora/kb1/assistant/avatar")
    assert seen[0].headers["content-type"].startswith("multipart/form-data")
    assert b'filename="asha.png"' in seen[0].content


# /BIZ4 avatar


# PIP1 profile
def test_pipeline_profile_calls() -> None:
    c = client()
    c.list_pipeline_profiles()
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/admin/pipelines")
    c.create_pipeline_profile("Fast", "Economy", config={"max_answer_tokens": 400})
    assert (calls[-1].method, calls[-1].url.path) == ("POST", "/api/v1/admin/pipelines")
    assert json.loads(calls[-1].content) == {
        "name": "Fast",
        "label": "Economy",
        "config": {"max_answer_tokens": 400},
    }
    c.update_pipeline_profile("pf1", label="Basic")
    assert (calls[-1].method, calls[-1].url.path) == ("PATCH", "/api/v1/admin/pipelines/pf1")
    assert json.loads(calls[-1].content) == {"label": "Basic"}
    c.get_pipeline_profile("pf1")
    c.delete_pipeline_profile("pf1")
    assert (calls[-1].method, calls[-1].url.path) == ("DELETE", "/api/v1/admin/pipelines/pf1")
    c.set_tenant_pipeline("t1", None)
    assert (calls[-1].method, calls[-1].url.path) == (
        "PUT",
        "/api/v1/admin/pipelines/tenants/t1",
    )
    assert json.loads(calls[-1].content) == {"profile_id": None}
    c.get_tenant_pipeline("t1")
    assert calls[-1].url.path == "/api/v1/admin/pipelines/tenants/t1"


# /PIP1 profile


# PIP3 fair use
def test_fair_use_calls() -> None:
    c = client()
    c.list_fair_use(state="throttled", limit=20)
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/admin/fair-use")
    assert dict(calls[-1].url.params) == {"state": "throttled", "limit": "20"}
    c.get_fair_use("t1")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/admin/fair-use/tenants/t1")
    c.set_fair_use(
        "t1", limits={"questions_per_hour": 100, "questions_per_day": None}, clear=["storage_mb"]
    )
    assert (calls[-1].method, calls[-1].url.path) == ("PUT", "/api/v1/admin/fair-use/tenants/t1")
    assert json.loads(calls[-1].content) == {
        "limits": {"questions_per_hour": 100, "questions_per_day": None},
        "policy": {},
        "clear": ["storage_mb"],
    }
    c.fair_use()
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/fair-use")


# /PIP3 fair use


# TV4 value report
def test_value_report_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.params.get("format") == "html":
            return httpx.Response(200, text="<p>Estimated value, based on your settings.</p>")
        return httpx.Response(200, json={"ok": True})

    c = CobRag("http://engine/", api_key="tk", transport=httpx.MockTransport(answer))
    assert c.value_settings() == {"ok": True}
    c.update_value_settings(
        lead_value_minor=150_000,
        handoff_value_minor=80_000,
        staff_hour_minor=25_000,
        minutes_per_answer=3,
        industry_defaults="clinic",
    )
    c.value_report()
    c.value_report("2026-09")
    assert "Estimated value" in c.value_report("2026-09", format="html")
    c.value_reports()
    c.admin_value_report("t1", "2026-09")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/value/settings"),
        ("PUT", "/api/v1/value/settings"),
        ("GET", "/api/v1/value/report"),
        ("GET", "/api/v1/value/report"),
        ("GET", "/api/v1/value/report"),
        ("GET", "/api/v1/value/reports"),
        ("GET", "/api/v1/admin/tenants/t1/value-report"),
    ]
    assert json.loads(seen[1].content) == {
        "lead_value_minor": 150_000,
        "handoff_value_minor": 80_000,
        "staff_hour_minor": 25_000,
        "minutes_per_answer": 3,
        "email_enabled": True,
        "industry_defaults": "clinic",
    }
    assert "month" not in seen[2].url.params and seen[3].url.params["month"] == "2026-09"
    assert seen[4].url.params["format"] == "html"
    assert seen[6].url.params["month"] == "2026-09"


async def test_async_value_report_calls() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    c = AsyncCobRag("http://engine/", api_key="tk", transport=httpx.MockTransport(answer))
    assert await c.value_settings() == {"ok": True}
    await c.update_value_settings(
        lead_value_minor=1, handoff_value_minor=2, staff_hour_minor=3, minutes_per_answer=4
    )
    await c.value_report("2026-09")
    await c.value_reports()
    await c.admin_value_report("t1")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/value/settings"),
        ("PUT", "/api/v1/value/settings"),
        ("GET", "/api/v1/value/report"),
        ("GET", "/api/v1/value/reports"),
        ("GET", "/api/v1/admin/tenants/t1/value-report"),
    ]


# /TV4 value report


# TV2 reference
def test_lookup_reference_method() -> None:
    c = client()
    c.lookup_reference("sd4f7k")
    assert (calls[-1].method, calls[-1].url.path) == ("GET", "/api/v1/handoffs/lookup")
    assert dict(calls[-1].url.params) == {"ref": "sd4f7k"}


async def test_async_lookup_reference_method() -> None:
    async with AsyncCobRag(
        "http://engine/", api_key="tk_x", transport=httpx.MockTransport(handler)
    ) as c:
        await c.lookup_reference("SD-4F7K")
        assert calls[-1].url.path == "/api/v1/handoffs/lookup"
        assert dict(calls[-1].url.params) == {"ref": "SD-4F7K"}


# /TV2 reference
