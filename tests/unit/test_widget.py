"""Embeddable chat widget: publishable keys, origin checks, rate limits, streaming."""

import json
import uuid
from datetime import UTC, datetime
from importlib import resources
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.schemas.widget import CreateWidgetKeyRequest, WidgetChatRequest
from callobuzz_rag.core.config import Settings
from callobuzz_rag.widget.security import (
    LimitResult,
    WidgetRateLimiter,
    client_ip,
    generate_public_key,
    normalize_origin,
    origin_allowed,
)

pytestmark = pytest.mark.usefixtures("unlimited_plan")

SITE = "https://acme.com"


# ---------------------------------------------------------------- pure helpers


class TestOrigins:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://Acme.com/", "https://acme.com"),
            ("http://localhost:8090", "http://localhost:8090"),
            ("  https://shop.acme.com  ", "https://shop.acme.com"),
            ("*", "*"),
        ],
    )
    def test_normalize(self, raw: str, expected: str) -> None:
        assert normalize_origin(raw) == expected

    @pytest.mark.parametrize(
        "bad", ["acme.com", "ftp://acme.com", "https://acme.com/help", "https://a.com?x=1"]
    )
    def test_normalize_rejects_non_origins(self, bad: str) -> None:
        with pytest.raises(ValueError):
            normalize_origin(bad)

    def test_allowed(self) -> None:
        assert origin_allowed("https://ACME.com", [SITE])
        assert not origin_allowed("https://evil.com", [SITE])
        assert not origin_allowed("https://acme.com.evil.com", [SITE])
        assert not origin_allowed(None, [SITE])  # non-browser callers need a ck_ key
        assert origin_allowed("https://anything.io", ["*"])

    def test_public_key_shape(self) -> None:
        key = generate_public_key()
        assert key.startswith("pk_") and len(key) == 35
        assert key != generate_public_key()

    def test_client_ip_ignores_forwarded_for_unless_trusted(self) -> None:
        request = SimpleNamespace(
            headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1"},
            client=SimpleNamespace(host="10.0.0.9"),
        )
        assert client_ip(request, trust_proxy_headers=False) == "10.0.0.9"  # type: ignore[arg-type]
        # One trusted proxy: the entry it appended (the last), not the caller's first.
        assert client_ip(request, trust_proxy_headers=True) == "10.0.0.1"  # type: ignore[arg-type]
        assert client_ip(request, trust_proxy_headers=True, trusted_hops=2) == "1.2.3.4"  # type: ignore[arg-type]


class TestSchemas:
    def test_create_normalizes_and_dedupes_origins(self) -> None:
        body = CreateWidgetKeyRequest(
            name="Site", allowed_origins=["https://Acme.com/", "https://acme.com"]
        )
        assert body.allowed_origins == [SITE]

    def test_create_requires_an_origin(self) -> None:
        with pytest.raises(ValidationError):
            CreateWidgetKeyRequest(name="Site", allowed_origins=[])

    def test_bad_color_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CreateWidgetKeyRequest(
                name="Site", allowed_origins=[SITE], appearance={"primary_color": "red"}
            )

    def test_chat_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            WidgetChatRequest(message="hi", model="gpt-4")  # type: ignore[call-arg]


class TestRateLimiter:
    def _limiter(
        self, minute: tuple[int, int] = (1, 0), day: int | None = None, ttl: int = 42
    ) -> WidgetRateLimiter:
        """minute = (this window's count after the hit, the previous window's count)."""
        limiter = WidgetRateLimiter.__new__(WidgetRateLimiter)
        pipes = []
        for result in ([minute[0], True, minute[1]], [day, True, ttl]):
            pipe = MagicMock()
            pipe.execute = AsyncMock(return_value=result)
            pipes.append(pipe)
        limiter._redis = MagicMock()
        limiter._redis.pipeline = MagicMock(side_effect=pipes)
        return limiter

    async def test_allows_under_both_limits(self) -> None:
        result = await self._limiter((1, 0), day=1).check("k", "ip", 10, 100)
        assert result.allowed

    async def test_per_minute_limit(self) -> None:
        result = await self._limiter((11, 0)).check("k", "ip", 10, 100)
        assert not result.allowed
        assert 1 <= result.retry_after <= 60
        assert "wait" in result.reason

    async def test_previous_window_still_counts_across_the_boundary(self) -> None:
        """The fixed-window hole: a full previous minute blocks the start of the next."""
        with patch("callobuzz_rag.widget.security.time.time", return_value=60 * 1000 + 6):
            result = await self._limiter((2, 10)).check("k", "ip", 10, 100)
        assert not result.allowed  # 2 + 10 * 0.9 = 11 > 10

    async def test_previous_window_fades_out(self) -> None:
        with patch("callobuzz_rag.widget.security.time.time", return_value=60 * 1000 + 54):
            result = await self._limiter((2, 10), day=1).check("k", "ip", 10, 100)
        assert result.allowed  # 2 + 10 * 0.1 = 3

    async def test_daily_limit(self) -> None:
        result = await self._limiter((1, 0), day=101).check("k", "ip", 10, 100)
        assert not result.allowed and "daily" in result.reason

    async def test_zero_daily_limit_means_unlimited(self) -> None:
        limiter = self._limiter((1, 0))
        assert (await limiter.check("k", "ip", 10, 0)).allowed
        assert limiter._redis.pipeline.call_count == 1  # daily counter never touched

    async def test_fails_open_when_redis_is_down(self) -> None:
        limiter = WidgetRateLimiter.__new__(WidgetRateLimiter)
        limiter._redis = MagicMock()
        limiter._redis.pipeline = MagicMock(side_effect=ConnectionError("down"))
        assert (await limiter.check("k", "ip", 10, 100)).allowed


# ------------------------------------------------------------------ routes


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        admin_api_key="test-admin-key",
        llm_api_key="test",
        public_url="https://rag.example.com",
    )


def _key(corpus_id: uuid.UUID, **overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "corpus_id": corpus_id,
        "name": "Acme site",
        "public_key": "pk_" + "a" * 32,
        "allowed_origins": [SITE],
        "rate_limit_per_minute": 10,
        "daily_message_limit": 1000,
        "appearance": {"title": "Acme help", "primary_color": "#112233"},
        "is_active": True,
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def corpus() -> SimpleNamespace:
    cid = uuid.uuid4()
    return SimpleNamespace(
        id=cid,
        tenant_id=uuid.uuid4(),
        qdrant_collection=f"corpus_{str(cid)[:8]}",
        retrieval_config=None,
        llm_config=None,
        prompt_config=None,
    )


@pytest.fixture
def app():
    application = create_app()
    application.state.settings = _settings()
    application.state.widget_rate_limiter = SimpleNamespace(
        check=AsyncMock(return_value=LimitResult(True))
    )
    return application


def _session(key: SimpleNamespace | None, corpus: SimpleNamespace) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = key
    session.execute = AsyncMock(return_value=result)
    # Every corpus has a tenant (accounts A1); widget auth reads it.
    tenant = SimpleNamespace(id=corpus.tenant_id, status="active")
    session.get = AsyncMock(
        side_effect=lambda model, _id: tenant if model.__name__ == "Tenant" else corpus
    )
    return session


def _use_session(app, session: AsyncMock) -> None:
    from callobuzz_rag.api.deps import get_session

    async def override():
        yield session

    app.dependency_overrides[get_session] = override


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestWidgetScript:
    async def test_served_without_any_key(self, app) -> None:
        async with await _client(app) as c:
            res = await c.get("/widget.js")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/javascript")
        assert "X-Widget-Key" in res.text and "attachShadow" in res.text

    async def test_bundle_includes_helpers_and_lead_form(self, app) -> None:
        async with await _client(app) as c:
            res = await c.get("/widget.js")
        assert "root.CobLeadForm" in res.text and "CobMd" in res.text
        assert "/api/v1/widget/lead" in res.text
        assert 'trapInput.name = "website"' in res.text  # the spam trap
        assert res.text.index("root.CobLeadForm =") < res.text.index("attachShadow")
        assert "function openLeadForm(" in res.text  # the hook C6's call-back button uses

    def test_widget_parts_are_the_pinned_six_in_order(self) -> None:
        from callobuzz_rag.api.routes.widget import WIDGET_PARTS

        assert WIDGET_PARTS == (
            "static/markdown.js",
            "static/widget-core.js",
            "static/lead_form.js",
            "static/handoff.js",
            "static/sensor.js",  # BIZ2 sensor
            "static/widget.js",
        )

    def test_the_lead_form_is_built_from_nodes_and_never_pre_ticked(self) -> None:
        script = (
            resources.files("callobuzz_rag.widget").joinpath("static/widget.js").read_text("utf-8")
        )
        start = script.index("function renderLeadForm(")
        end = script.index("function thanks(")
        form = script[start:end]
        # The one innerHTML is the owner's consent notice, escaped by CobMd.inline.
        assert form.count("innerHTML") == 1 and "MD.inline(block.notice)" in form
        assert "consent.checked = false" in form and "consent.checked = true" not in form
        assert 'trap.setAttribute("aria-hidden", "true")' in form and "tabIndex = -1" in form
        assert "website: trapInput.value" in form
        assert "X-Widget-Key" in form and "session: getSession()" in form
        # No raw server text on a failure: a generic, localised line.
        assert "m.failed" in form

    def test_the_form_follows_the_answer_and_a_ui_event_only_stores_it(self) -> None:
        script = (
            resources.files("callobuzz_rag.widget").joinpath("static/widget.js").read_text("utf-8")
        )
        assert (
            'payload.kind === "lead_form" || payload.kind === "handoff_card") pendingUi = payload;'
            in script
        )
        # Opened after the answer is complete (and stored), never during it.
        assert script.index("history.push(turn)") < script.index(
            "else if (pendingUi) openLeadForm(pendingUi)"
        )


class TestWidgetStageWords:
    """The typing indicator names the stage after a 2 s wait; the words come from the script."""

    def test_every_engine_stage_has_plain_words_and_the_wait_threshold(self) -> None:
        from callobuzz_rag.api.stage_events import (
            STAGE_CHECK,
            STAGE_FORM,
            STAGE_HANDOFF,
            STAGE_RERANK,
            STAGE_SEARCH,
            STAGE_UNDERSTAND,
            STAGE_VERIFY,
            STAGE_WRITE,
        )

        static = resources.files("callobuzz_rag.widget").joinpath("static")
        script = static.joinpath("widget.js").read_text("utf-8")
        core = static.joinpath("widget-core.js").read_text("utf-8")
        for stage in (
            STAGE_UNDERSTAND,
            STAGE_SEARCH,
            STAGE_RERANK,
            STAGE_CHECK,
            STAGE_WRITE,
            STAGE_VERIFY,
            STAGE_FORM,
            STAGE_HANDOFF,
        ):
            assert f"      stage_{stage}: " in core  # the words, in the English table
        assert "var STAGE_AFTER_MS = 2000;" in script
        assert 'type === "stage"' in script
        assert "Searching your documents" in core
        # Words for a visitor: no jargon from the engine.
        assert "stage_rerank:" in core and "Picking the best matches" in core


class TestPreflight:
    async def test_widget_preflight_reflects_origin(self, app) -> None:
        async with await _client(app) as c:
            res = await c.options(
                "/api/v1/widget/chat",
                headers={
                    "Origin": "https://customer.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,x-widget-key",
                },
            )
        assert res.status_code == 204
        assert res.headers["access-control-allow-origin"] == "https://customer.example"
        assert "X-Widget-Key" in res.headers["access-control-allow-headers"]


class TestWidgetConfig:
    async def test_returns_branding_merged_with_defaults(self, app, corpus) -> None:
        _use_session(app, _session(_key(corpus.id), corpus))
        async with await _client(app) as c:
            res = await c.get(
                "/api/v1/widget/config",
                headers={"X-Widget-Key": "pk_" + "a" * 32, "Origin": SITE},
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["title"] == "Acme help"
        assert body["primary_color"] == "#112233"
        assert body["greeting"]  # default filled in
        assert res.headers["access-control-allow-origin"] == SITE

    async def test_missing_key_is_401(self, app, corpus) -> None:
        _use_session(app, _session(None, corpus))
        async with await _client(app) as c:
            res = await c.get("/api/v1/widget/config", headers={"Origin": SITE})
        assert res.status_code == 401

    async def test_unknown_key_is_401(self, app, corpus) -> None:
        _use_session(app, _session(None, corpus))
        async with await _client(app) as c:
            res = await c.get(
                "/api/v1/widget/config", headers={"X-Widget-Key": "pk_nope", "Origin": SITE}
            )
        assert res.status_code == 401

    async def test_disabled_key_is_401(self, app, corpus) -> None:
        _use_session(app, _session(_key(corpus.id, is_active=False), corpus))
        async with await _client(app) as c:
            res = await c.get(
                "/api/v1/widget/config", headers={"X-Widget-Key": "pk_x", "Origin": SITE}
            )
        assert res.status_code == 401

    async def test_other_website_is_403(self, app, corpus) -> None:
        _use_session(app, _session(_key(corpus.id), corpus))
        async with await _client(app) as c:
            res = await c.get(
                "/api/v1/widget/config",
                headers={"X-Widget-Key": "pk_x", "Origin": "https://evil.example"},
            )
        assert res.status_code == 403

    async def test_ck_api_key_is_not_needed_and_not_accepted(self, app, corpus) -> None:
        _use_session(app, _session(None, corpus))
        async with await _client(app) as c:
            res = await c.get(
                "/api/v1/widget/config",
                headers={"X-Widget-Key": "ck_secret", "Origin": SITE},
            )
        assert res.status_code == 401  # secret keys are never widget keys


def _orchestrator(tokens: list[str], *, fail: bool = False) -> MagicMock:
    cited = [
        SimpleNamespace(
            citation_index=1,
            file_name="returns.pdf",
            section="Returns > Time limits",
            page=2,
            text="Return within 30 days",
        ),
    ]
    messages = [
        SimpleNamespace(role="system", content="sys"),
        SimpleNamespace(role="user", content="ctx + question"),
    ]
    pipeline = SimpleNamespace(assembled=SimpleNamespace(messages=messages, cited_chunks=cited))

    async def stream(msgs):
        for token in tokens:
            yield token
        if fail:
            raise RuntimeError("provider down")

    orch = MagicMock()
    orch.run_retrieval_pipeline = AsyncMock(return_value=pipeline)
    orch.llm.complete_stream = MagicMock(side_effect=stream)
    return orch


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


class TestWidgetChat:
    async def _chat(self, app, corpus, orch, body=None, key=None, language_models=None):
        _use_session(app, _session(key or _key(corpus.id), corpus))
        with (
            patch("callobuzz_rag.api.routes.widget._build_orchestrator", return_value=orch),
            patch("callobuzz_rag.api.routes.widget._get_config_resolver") as resolver,
        ):
            resolver.return_value.resolve_retrieval.return_value = MagicMock()
            resolver.return_value.resolve_llm.return_value = MagicMock(
                language_models=language_models or {}, model="engine/model"
            )
            async with await _client(app) as c:
                return await c.post(
                    "/api/v1/widget/chat",
                    json=body or {"message": "Can I return this?"},
                    headers={"X-Widget-Key": "pk_x", "Origin": SITE},
                )

    async def test_streams_sources_then_tokens_then_done(self, app, corpus) -> None:
        orch = _orchestrator(["Yes, ", "within 30 days."])
        res = await self._chat(app, corpus, orch)
        assert res.status_code == 200, res.text
        assert res.headers["content-type"].startswith("text/event-stream")
        events = _events(res.text)
        # Progress first: the widget turns a long wait into words like "Searching...".
        stages = [e[1]["stage"] for e in events if e[0] == "stage"]
        assert stages == ["understand", "write"]
        assert [e[0] for e in events].index("sources") < [e[0] for e in events].index("token")
        events = [e for e in events if e[0] != "stage"]
        assert events[0] == (
            "sources",
            {
                "sources": [
                    {
                        "index": 1,
                        "file_name": "returns.pdf",
                        "section": "Returns > Time limits",
                        "page": 2,
                        "snippet": "Return within 30 days",
                    }
                ]
            },
        )
        assert "".join(e[1]["content"] for e in events if e[0] == "token") == (
            "Yes, within 30 days."
        )
        assert events[-1][0] == "done"

    async def test_history_is_replayed_before_the_grounded_question(self, app, corpus) -> None:
        orch = _orchestrator(["ok"])
        body = {
            "message": "and for shoes?",
            "history": [
                {"role": "user", "content": "return policy?"},
                {"role": "assistant", "content": "30 days."},
            ],
        }
        await self._chat(app, corpus, orch, body)
        sent = orch.llm.complete_stream.call_args.args[0]
        assert [m.role for m in sent] == ["system", "user", "assistant", "user"]
        assert sent[-1].content == "ctx + question"  # grounded prompt stays last
        # Retrieval searches the visitor's actual message.
        assert orch.run_retrieval_pipeline.await_args.kwargs["text"] == "and for shoes?"

    async def test_browser_cannot_choose_the_llm(self, app, corpus) -> None:
        orch = _orchestrator(["ok"])
        _use_session(app, _session(_key(corpus.id), corpus))
        with (
            patch("callobuzz_rag.api.routes.widget._build_orchestrator", return_value=orch),
            patch("callobuzz_rag.api.routes.widget._get_config_resolver") as resolver,
        ):
            async with await _client(app) as c:
                await c.post(
                    "/api/v1/widget/chat",
                    json={"message": "hi"},
                    headers={
                        "X-Widget-Key": "pk_x",
                        "Origin": SITE,
                        "X-LLM-Model": "expensive-model",
                        "X-LLM-API-Key": "stolen",
                    },
                )
        assert resolver.return_value.resolve_llm.call_args.kwargs["request_headers"] is None

    async def test_a_hindi_message_is_answered_on_the_routed_model_and_metered_under_it(
        self, app, corpus
    ) -> None:
        orch = _orchestrator(["engine"])

        async def routed_stream(msgs):
            yield "namaste"

        routed = MagicMock()
        routed.complete_stream = MagicMock(side_effect=routed_stream)
        orch.llm.for_model = MagicMock(return_value=routed)
        recorded: list[dict] = []

        async def fake_record(session_factory, **kwargs):
            recorded.append(kwargs)

        with patch("callobuzz_rag.api.routes.widget.record_usage_new_session", fake_record):
            res = await self._chat(
                app,
                corpus,
                orch,
                {"message": "मुझे रिटर्न पॉलिसी बताओ"},
                language_models={"hi": "google/gemma-4-31b-it"},
            )
        assert "namaste" in res.text
        orch.llm.for_model.assert_called_once_with("google/gemma-4-31b-it", "off")
        orch.llm.complete_stream.assert_not_called()
        assert [r["model"] for r in recorded] == ["google/gemma-4-31b-it"]
        assert isinstance(recorded[0]["request_id"], uuid.UUID)  # BIL3 request id

    async def test_an_english_message_stays_on_the_engine_model(self, app, corpus) -> None:
        orch = _orchestrator(["engine"])
        res = await self._chat(
            app,
            corpus,
            orch,
            {"message": "Can I return this?"},
            language_models={"hi": "google/gemma-4-31b-it"},
        )
        assert "engine" in res.text
        orch.llm.for_model.assert_not_called()

    async def test_sources_hidden_when_disabled(self, app, corpus) -> None:
        key = _key(corpus.id, appearance={"show_sources": False})
        res = await self._chat(app, corpus, _orchestrator(["ok"]), key=key)
        assert [e[0] for e in _events(res.text) if e[0] != "stage"] == ["token", "done"]

    async def test_search_failure_becomes_error_event(self, app, corpus) -> None:
        orch = _orchestrator(["never"])
        orch.run_retrieval_pipeline = AsyncMock(side_effect=RuntimeError("qdrant: secret-host"))
        res = await self._chat(app, corpus, orch)
        events = _events(res.text)
        assert events[-1] == ("error", {"detail": "The assistant could not search its documents"})
        assert "secret-host" not in res.text
        orch.llm.complete_stream.assert_not_called()

    async def test_generation_failure_becomes_error_event(self, app, corpus) -> None:
        res = await self._chat(app, corpus, _orchestrator(["partial"], fail=True))
        events = _events(res.text)
        assert events[-1][0] == "error"
        assert "provider down" not in res.text  # internals never reach the visitor

    async def test_rate_limited_is_429_with_retry_after(self, app, corpus) -> None:
        app.state.widget_rate_limiter.check = AsyncMock(
            return_value=LimitResult(False, 17, "Too many messages. Please wait a moment.")
        )
        orch = _orchestrator(["never"])
        res = await self._chat(app, corpus, orch)
        assert res.status_code == 429
        assert res.headers["retry-after"] == "17"
        orch.run_retrieval_pipeline.assert_not_awaited()  # no LLM spend when limited

    async def test_other_website_never_reaches_the_pipeline(self, app, corpus) -> None:
        orch = _orchestrator(["never"])
        _use_session(app, _session(_key(corpus.id), corpus))
        with patch("callobuzz_rag.api.routes.widget._build_orchestrator", return_value=orch):
            async with await _client(app) as c:
                res = await c.post(
                    "/api/v1/widget/chat",
                    json={"message": "hi"},
                    headers={"X-Widget-Key": "pk_x", "Origin": "https://evil.example"},
                )
        assert res.status_code == 403
        orch.run_retrieval_pipeline.assert_not_awaited()


class TestManagement:
    @pytest.fixture
    def admin_app(self, app):
        from callobuzz_rag.api.auth import APIKeyAuthMiddleware

        original = APIKeyAuthMiddleware.dispatch

        async def as_admin(self, request, call_next):
            request.state.is_admin = True
            request.state.corpus = None
            request.state.tenant = None
            return await call_next(request)

        APIKeyAuthMiddleware.dispatch = as_admin
        yield app
        APIKeyAuthMiddleware.dispatch = original

    async def test_create_returns_key_and_embed_snippet(self, admin_app, corpus) -> None:
        session = _session(None, corpus)
        session.add = MagicMock()

        async def refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(UTC)
            obj.is_active = True

        session.refresh = AsyncMock(side_effect=refresh)
        _use_session(admin_app, session)
        async with await _client(admin_app) as c:
            res = await c.post(
                f"/api/v1/corpora/{corpus.id}/widget-keys",
                json={"name": "Acme", "allowed_origins": ["https://Acme.com/"]},
            )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["public_key"].startswith("pk_")
        assert body["allowed_origins"] == [SITE]
        assert body["embed_snippet"] == (
            f'<script src="https://rag.example.com/widget.js" '
            f'data-key="{body["public_key"]}" async></script>'
        )

    async def test_corpus_key_cannot_manage_another_corpus(self, app, corpus) -> None:
        from callobuzz_rag.api.auth import APIKeyAuthMiddleware

        original = APIKeyAuthMiddleware.dispatch
        other = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())

        async def as_other_corpus(self, request, call_next):
            request.state.is_admin = False
            request.state.corpus = other
            request.state.tenant = None
            return await call_next(request)

        APIKeyAuthMiddleware.dispatch = as_other_corpus
        try:
            _use_session(app, _session(None, corpus))
            async with await _client(app) as c:
                res = await c.get(f"/api/v1/corpora/{corpus.id}/widget-keys")
        finally:
            APIKeyAuthMiddleware.dispatch = original
        assert res.status_code == 403


class TestSuspendedTenant:
    @pytest.mark.parametrize("status", ["pending", "suspended"])
    @pytest.mark.parametrize("route", ["config", "chat"])
    async def test_widget_of_an_inactive_tenant_is_refused_politely(
        self, app, status: str, route: str
    ) -> None:
        cid = uuid.uuid4()
        corpus = SimpleNamespace(
            id=cid,
            tenant_id=uuid.uuid4(),
            qdrant_collection="c",
            retrieval_config=None,
            llm_config=None,
            prompt_config=None,
        )
        session = _session(_key(cid), corpus)
        tenant = SimpleNamespace(id=corpus.tenant_id, status=status)
        session.get = AsyncMock(
            side_effect=lambda model, _id: tenant if model.__name__ == "Tenant" else corpus
        )
        _use_session(app, session)
        headers = {"X-Widget-Key": "pk_x", "Origin": SITE}
        async with await _client(app) as c:
            if route == "config":
                res = await c.get("/api/v1/widget/config", headers=headers)
            else:
                res = await c.post("/api/v1/widget/chat", json={"message": "hi"}, headers=headers)
        app.state.widget_rate_limiter.check.assert_not_called()  # refused before any work
        assert res.status_code == 403
        # No `status`: website visitors must not learn a business is suspended.
        assert res.json() == {
            "detail": "This assistant is not available right now.",
            "code": "workspace_not_active",
        }
