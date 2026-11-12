"""Unit tests for API Key authentication middleware.

Tests cover: public path bypass, missing/invalid key rejection, admin key
recognition, corpus key resolution (named keys in `api_keys`: lookup, cache, last_used_at,
permission), dependency helpers (get_authenticated_corpus, require_admin), and bcrypt
timing sanity.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from callobuzz_rag.api.auth import (
    LANDING_PREFIXES,  # LP1 landing
    LEGAL_PREFIXES,
    OPEN_PREFIXES,
    PUBLIC_PATHS,
    PUBLIC_PREFIXES,
    WIDGET_PREFIXES,
    APIKeyAuthMiddleware,
    _WorkspaceNotActiveError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    path: str = "/api/v1/query",
    api_key: str | None = None,
    is_admin: bool | None = None,
    corpus: object | None = None,
) -> MagicMock:
    """Create a mock Request with the required attributes."""
    request = MagicMock()
    request.url.path = path
    request.headers = {}
    if api_key is not None:
        request.headers["X-API-Key"] = api_key

    # State object with setattr support
    state = MagicMock()
    if is_admin is not None:
        state.is_admin = is_admin
    else:
        # Simulate no auth state
        del state.is_admin
    if corpus is not None:
        state.corpus = corpus
    else:
        del state.corpus
    request.state = state

    # App state
    settings = MagicMock()
    from pydantic import SecretStr

    settings.admin_api_key = SecretStr("admin-secret-key-12345")
    request.app.state.settings = settings
    request.app.state.session_factory = MagicMock()

    return request


def _make_corpus(
    corpus_id: str | None = None,
    api_key_hash: str = "hashed",
) -> MagicMock:
    """Create a mock Corpus object."""
    corpus = MagicMock()
    corpus.id = corpus_id or uuid4()
    corpus.name = "Test Corpus"
    corpus.api_key_hash = api_key_hash
    # Tests whose session.get returns this mock for every model read it as the tenant too.
    corpus.status = "active"
    return corpus


# ---------------------------------------------------------------------------
# 1. Public paths bypass auth
# ---------------------------------------------------------------------------


class TestPublicPaths:
    @pytest.mark.asyncio
    async def test_health_endpoint_no_auth_needed(self) -> None:
        """Health endpoint should be accessible without any API key."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        request = _make_request(path="/api/v1/health")

        async def call_next(req: MagicMock) -> MagicMock:
            response = MagicMock()
            response.status_code = 200
            return response

        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_docs_endpoint_no_auth_needed(self) -> None:
        """Docs endpoints should be accessible without any API key."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())

        for path in ["/docs", "/openapi.json", "/redoc"]:
            request = _make_request(path=path)

            async def call_next(req: MagicMock) -> MagicMock:
                response = MagicMock()
                response.status_code = 200
                return response

            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200, f"Path {path} should be public"

    def test_public_paths_set_contains_expected(self) -> None:
        """PUBLIC_PATHS should contain health, docs, openapi, redoc."""
        assert "/api/v1/health" in PUBLIC_PATHS
        assert "/docs" in PUBLIC_PATHS
        assert "/openapi.json" in PUBLIC_PATHS
        assert "/redoc" in PUBLIC_PATHS

    def test_public_prefixes_are_widget_plus_open(self) -> None:
        """Widget routes check their own pk_ key; /api/v1/public/ needs no credential."""
        assert WIDGET_PREFIXES == ("/api/v1/widget/",)
        assert OPEN_PREFIXES == ("/api/v1/public/",)
        assert LEGAL_PREFIXES == ("/legal/",)
        # LP1 landing
        assert PUBLIC_PREFIXES == (
            WIDGET_PREFIXES + OPEN_PREFIXES + LEGAL_PREFIXES + LANDING_PREFIXES
        )
        assert LANDING_PREFIXES == ("/_landing/",)
        for path in (
            "/",
            "/pricing",
            "/hi",
            "/hi/",
            "/hi/contact",
            "/hi-latn/about",
            "/robots.txt",
        ):
            assert path in PUBLIC_PATHS, path
        assert "/api/v1/admin/landing" not in PUBLIC_PATHS
        # /LP1 landing
        assert "/api/v1/public/" in PUBLIC_PREFIXES

    @pytest.mark.asyncio
    async def test_open_prefix_skips_auth_and_sets_no_scope(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        request = _make_request(path="/api/v1/public/branding")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200
        call_next.assert_awaited_once()
        assert request.state.is_admin is False
        assert request.state.tenant is None
        assert request.state.corpus is None


# ---------------------------------------------------------------------------
# 2-4. Missing and invalid keys
# ---------------------------------------------------------------------------


class TestKeyValidation:
    @pytest.mark.asyncio
    async def test_missing_api_key_returns_401(self) -> None:
        """Request without X-API-Key header should get 401."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        request = _make_request(path="/api/v1/query", api_key=None)

        async def call_next(req: MagicMock) -> MagicMock:
            return MagicMock(status_code=200)

        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_401(self) -> None:
        """Request with wrong API key should get 401."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        request = _make_request(path="/api/v1/query", api_key="wrong-key-entirely")

        with patch.object(
            middleware,
            "_find_corpus_by_key",
            new_callable=AsyncMock,
            return_value=None,
        ):

            async def call_next(req: MagicMock) -> MagicMock:
                return MagicMock(status_code=200)

            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 401


# ---------------------------------------------------------------------------
# 5-6. Admin key
# ---------------------------------------------------------------------------


class TestAdminKey:
    @pytest.mark.asyncio
    async def test_admin_key_sets_is_admin(self) -> None:
        """Valid admin key should set request.state.is_admin = True."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        request = _make_request(
            path="/api/v1/corpora",
            api_key="admin-secret-key-12345",
        )

        captured_state: dict = {}

        async def call_next(req: MagicMock) -> MagicMock:
            captured_state["is_admin"] = req.state.is_admin
            captured_state["corpus"] = req.state.corpus
            response = MagicMock()
            response.status_code = 200
            return response

        await middleware.dispatch(request, call_next)
        assert captured_state["is_admin"] is True

    @pytest.mark.asyncio
    async def test_admin_key_corpus_is_none(self) -> None:
        """Admin key should set request.state.corpus = None."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        request = _make_request(
            path="/api/v1/corpora",
            api_key="admin-secret-key-12345",
        )

        captured_state: dict = {}

        async def call_next(req: MagicMock) -> MagicMock:
            captured_state["corpus"] = req.state.corpus
            response = MagicMock()
            response.status_code = 200
            return response

        await middleware.dispatch(request, call_next)
        assert captured_state["corpus"] is None


# ---------------------------------------------------------------------------
# 7-8. Corpus key
# ---------------------------------------------------------------------------


class TestCorpusKey:
    @pytest.mark.asyncio
    async def test_corpus_key_sets_corpus(self) -> None:
        """Valid corpus key should set request.state.corpus to the Corpus object."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        mock_corpus = _make_corpus()
        request = _make_request(
            path="/api/v1/query",
            api_key="corpus-api-key-xyz",
        )

        with patch.object(
            middleware,
            "_find_corpus_by_key",
            new_callable=AsyncMock,
            return_value=mock_corpus,
        ):
            captured_state: dict = {}

            async def call_next(req: MagicMock) -> MagicMock:
                captured_state["corpus"] = req.state.corpus
                response = MagicMock()
                response.status_code = 200
                return response

            await middleware.dispatch(request, call_next)
            assert captured_state["corpus"] is mock_corpus

    @pytest.mark.asyncio
    async def test_corpus_key_not_admin(self) -> None:
        """Corpus key should set request.state.is_admin = False."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        mock_corpus = _make_corpus()
        request = _make_request(
            path="/api/v1/query",
            api_key="corpus-api-key-xyz",
        )

        with patch.object(
            middleware,
            "_find_corpus_by_key",
            new_callable=AsyncMock,
            return_value=mock_corpus,
        ):
            captured_state: dict = {}

            async def call_next(req: MagicMock) -> MagicMock:
                captured_state["is_admin"] = req.state.is_admin
                response = MagicMock()
                response.status_code = 200
                return response

            await middleware.dispatch(request, call_next)
            assert captured_state["is_admin"] is False


# ---------------------------------------------------------------------------
# 8b. Prefix-based O(1) corpus lookup
# ---------------------------------------------------------------------------


def _key_row(
    *,
    key_hash: str = "hash-v1",
    permission: str = "full",
    name: str = "Default key",
    lookup: str | None = None,
) -> MagicMock:
    """A mock `api_keys` row."""
    row = MagicMock()
    row.id = uuid4()
    row.key_hash = key_hash
    row.permission = permission
    row.name = name
    row.lookup = lookup
    return row


def _key_session(row: object, owner: object, tenant: object | None = None) -> AsyncMock:
    """A session whose joined (key, owner) query finds `row` and `owner`; `session.get` answers
    the owner's tenant (corpus keys) with `tenant`, else with `owner`."""
    from callobuzz_rag.db.models.tenant import Tenant

    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = [(row, owner)]
    result.first.return_value = (row, owner)
    session.execute.return_value = result

    async def _get(model: object, _id: object) -> object | None:
        return (tenant if tenant is not None else owner) if model is Tenant else owner

    session.get = AsyncMock(side_effect=_get)
    session.expunge = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestPrefixLookup:
    @pytest.mark.asyncio
    async def test_lookup_finds_the_key_row_by_id_segment_and_lookup(self) -> None:
        """One query for the owner's candidate rows; bcrypt runs on the one that matches."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        mock_corpus = _make_corpus()
        row = _key_row(permission="read", name="Agent - Claude Code", lookup="randomse")
        api_key = "ck_abcd1234_randomsecretpart"
        request = _make_request(path="/api/v1/query", api_key=api_key)
        session = _key_session(row, mock_corpus, tenant=MagicMock(status="active"))
        request.app.state.session_factory.return_value = session

        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=True) as verify:
            result = await middleware._find_corpus_by_key(request, api_key)

        assert result is mock_corpus
        verify.assert_called_once_with(api_key, "hash-v1")
        info = result._matched_key  # type: ignore[union-attr]
        assert (info.id, info.name, info.permission, info.kind) == (
            row.id,
            "Agent - Claude Code",
            "read",
            "ck",
        )
        statement = str(session.execute.await_args_list[0].args[0])
        assert "api_keys.id_short" in statement and "api_keys.lookup" in statement

    @pytest.mark.asyncio
    async def test_a_key_that_does_not_verify_is_none(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        request = _make_request(api_key="ck_abcd1234_wrong")
        request.app.state.session_factory.return_value = _key_session(_key_row(), _make_corpus())
        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=False):
            assert await middleware._find_corpus_by_key(request, "ck_abcd1234_wrong") is None

    @pytest.mark.asyncio
    async def test_a_tk_key_is_never_looked_up_as_a_ck_key(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        request = _make_request(api_key="tk_abcd1234_secret")
        with patch("callobuzz_rag.api.auth.verify_api_key") as verify:
            assert await middleware._find_corpus_by_key(request, "tk_abcd1234_secret") is None
            assert await middleware._find_tenant_by_key(request, "ck_abcd1234_secret") is None
        verify.assert_not_called()
        request.app.state.session_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_key_without_the_id_prefix_is_rejected_without_a_scan(self) -> None:
        """No scan over every stored hash: that let one made-up key cost N bcrypts."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        request = _make_request(path="/api/v1/query", api_key="legacy-key-format-no-prefix")

        with patch("callobuzz_rag.api.auth.verify_api_key") as verify:
            result = await middleware._find_corpus_by_key(request, "legacy-key-format-no-prefix")

        assert result is None
        verify.assert_not_called()
        request.app.state.session_factory.assert_not_called()


class TestVerifiedKeyCache:
    """bcrypt once per key per process, never for a rotated or revoked key."""

    @pytest.mark.asyncio
    async def test_second_request_skips_bcrypt(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        corpus = _make_corpus()
        api_key = "ck_abcd1234_secret"
        request = _make_request(api_key=api_key)
        request.app.state.session_factory.return_value = _key_session(_key_row(), corpus)

        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=True) as verify:
            assert await middleware._find_corpus_by_key(request, api_key) is corpus
            assert await middleware._find_corpus_by_key(request, api_key) is corpus
        assert verify.call_count == 1

    @pytest.mark.asyncio
    async def test_rotation_ends_the_cached_verification(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        corpus = _make_corpus()
        row = _key_row(key_hash="hash-v1")
        api_key = "ck_abcd1234_secret"
        request = _make_request(api_key=api_key)
        request.app.state.session_factory.return_value = _key_session(row, corpus)

        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=True):
            assert await middleware._find_corpus_by_key(request, api_key) is corpus
        row.key_hash = "hash-v2"  # the key was rotated
        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=False) as verify:
            assert await middleware._find_corpus_by_key(request, api_key) is None
        verify.assert_called_once()  # fell back to a real check, which failed

    @pytest.mark.asyncio
    async def test_revoking_ends_the_cached_verification(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        corpus = _make_corpus()
        api_key = "ck_abcd1234_secret"
        request = _make_request(api_key=api_key)
        session = _key_session(_key_row(), corpus)
        request.app.state.session_factory.return_value = session
        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=True):
            assert await middleware._find_corpus_by_key(request, api_key) is corpus
            gone = MagicMock()
            gone.first.return_value = None
            gone.all.return_value = []
            session.execute.return_value = gone  # the row was deleted
            assert await middleware._find_corpus_by_key(request, api_key) is None

    @pytest.mark.asyncio
    async def test_the_cache_never_holds_the_key(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        corpus = _make_corpus()
        api_key = "ck_abcd1234_secret"
        request = _make_request(api_key=api_key)
        request.app.state.session_factory.return_value = _key_session(_key_row(), corpus)
        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=True):
            await middleware._find_corpus_by_key(request, api_key)
        assert api_key not in repr(middleware._verified._entries)


class TestLastUsed:
    """`last_used_at` is written at most once a minute per key, and never fails a request."""

    def _updates(self, session: AsyncMock) -> int:
        return sum(
            1
            for call in session.execute.await_args_list
            if str(call.args[0]).startswith("UPDATE api_keys")
        )

    @pytest.mark.asyncio
    async def test_written_once_then_throttled(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        api_key = "ck_abcd1234_secret"
        request = _make_request(api_key=api_key)
        session = _key_session(_key_row(), _make_corpus())
        request.app.state.session_factory.return_value = session
        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=True):
            for _ in range(5):
                await middleware._find_corpus_by_key(request, api_key)
        assert self._updates(session) == 1

    @pytest.mark.asyncio
    async def test_written_again_after_a_minute(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        api_key = "ck_abcd1234_secret"
        request = _make_request(api_key=api_key)
        session = _key_session(_key_row(), _make_corpus())
        request.app.state.session_factory.return_value = session
        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=True):
            await middleware._find_corpus_by_key(request, api_key)
            for key_id in middleware._touched:
                middleware._touched[key_id] -= 61
            await middleware._find_corpus_by_key(request, api_key)
        assert self._updates(session) == 2

    @pytest.mark.asyncio
    async def test_a_failed_write_does_not_fail_the_request(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        corpus = _make_corpus()
        api_key = "ck_abcd1234_secret"
        request = _make_request(api_key=api_key)
        session = _key_session(_key_row(), corpus)
        session.commit = AsyncMock(side_effect=RuntimeError("db is busy"))
        request.app.state.session_factory.return_value = session
        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=True):
            assert await middleware._find_corpus_by_key(request, api_key) is corpus
        session.rollback.assert_awaited()


class TestDeactivatedTenant:
    """A corpus key stops working the moment its tenant is pending or suspended (A4)."""

    def _corpus_of(self, tenant_id: object) -> MagicMock:
        corpus = _make_corpus()
        corpus.tenant_id = tenant_id
        return corpus

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["pending", "suspended"])
    async def test_key_of_inactive_tenant_is_refused_with_its_status(self, status: str) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        tenant = MagicMock(status=status)
        corpus = self._corpus_of(uuid4())
        request = _make_request(api_key="ck_abcd1234_secret")
        request.app.state.session_factory.return_value = _key_session(_key_row(), corpus, tenant)
        with (
            patch("callobuzz_rag.api.auth.verify_api_key", return_value=True),
            pytest.raises(_WorkspaceNotActiveError) as caught,
        ):
            await middleware._find_corpus_by_key(request, "ck_abcd1234_secret")
        assert caught.value.status == status

    @pytest.mark.asyncio
    async def test_suspension_applies_to_an_already_cached_key(self) -> None:
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        tenant = MagicMock(status="active")
        corpus = self._corpus_of(uuid4())
        api_key = "ck_abcd1234_secret"
        request = _make_request(api_key=api_key)
        request.app.state.session_factory.return_value = _key_session(_key_row(), corpus, tenant)
        with patch("callobuzz_rag.api.auth.verify_api_key", return_value=True):
            assert await middleware._find_corpus_by_key(request, api_key) is corpus
            tenant.status = "suspended"
            with pytest.raises(_WorkspaceNotActiveError):
                await middleware._find_corpus_by_key(request, api_key)
            tenant.status = "active"  # reactivated: works on the next request
            assert await middleware._find_corpus_by_key(request, api_key) is corpus

    @pytest.mark.asyncio
    async def test_dispatch_answers_403_with_code_and_status(self) -> None:
        import json

        middleware = APIKeyAuthMiddleware(app=MagicMock())
        middleware._find_corpus_by_key = AsyncMock(  # type: ignore[method-assign]
            side_effect=_WorkspaceNotActiveError("pending")
        )
        request = _make_request(api_key="ck_abcd1234_secret")
        request.method = "GET"
        call_next = AsyncMock()
        res = await middleware.dispatch(request, call_next)
        assert res.status_code == 403
        assert json.loads(res.body) == {
            "detail": "This workspace is not active",
            "code": "workspace_not_active",
            "status": "pending",
        }
        call_next.assert_not_called()


class TestKeyPermissionInDispatch:
    """After a key authenticated, its permission decides whether the route is reachable."""

    def _with_key(self, permission: str, method: str, path: str) -> tuple[object, MagicMock]:
        from callobuzz_rag.api.permissions import KeyInfo

        middleware = APIKeyAuthMiddleware(app=MagicMock())
        corpus = _make_corpus()
        corpus._matched_key = KeyInfo(uuid4(), "Agent - Cursor", permission, "ck")
        middleware._find_corpus_by_key = AsyncMock(return_value=corpus)  # type: ignore[method-assign]
        request = _make_request(path=path, api_key="ck_abcd1234_secret")
        request.method = method
        return middleware, request

    @pytest.mark.asyncio
    async def test_read_key_may_ask_but_not_ingest(self) -> None:
        import json

        middleware, request = self._with_key("read", "POST", "/api/v1/query")
        ok = await middleware.dispatch(request, AsyncMock(return_value=MagicMock(status_code=200)))  # type: ignore[attr-defined]
        assert ok.status_code == 200

        middleware, request = self._with_key("read", "POST", "/api/v1/ingest")
        call_next = AsyncMock()
        res = await middleware.dispatch(request, call_next)  # type: ignore[attr-defined]
        assert res.status_code == 403
        assert json.loads(res.body) == {
            "detail": "This API key has read permission; this needs write",
            "code": "key_permission_denied",
            "permission": "read",
            "required": "write",
        }
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_key_cannot_delete_and_full_can(self) -> None:
        middleware, request = self._with_key("write", "DELETE", "/api/v1/documents/abc")
        res = await middleware.dispatch(request, AsyncMock())  # type: ignore[attr-defined]
        assert res.status_code == 403 and b'"required":"full"' in res.body

        middleware, request = self._with_key("full", "DELETE", "/api/v1/documents/abc")
        res = await middleware.dispatch(  # type: ignore[attr-defined]
            request, AsyncMock(return_value=MagicMock(status_code=204))
        )
        assert res.status_code == 204

    @pytest.mark.asyncio
    async def test_state_carries_the_key(self) -> None:
        middleware, request = self._with_key("write", "POST", "/api/v1/ingest")
        seen: dict[str, object] = {}

        async def call_next(req: MagicMock) -> MagicMock:
            seen["info"] = req.state.api_key
            return MagicMock(status_code=202)

        await middleware.dispatch(request, call_next)  # type: ignore[attr-defined]
        assert seen["info"].name == "Agent - Cursor"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 9. Empty admin key setting
# ---------------------------------------------------------------------------


class TestEmptyAdminKey:
    @pytest.mark.asyncio
    async def test_empty_admin_key_setting_skips_admin_check(self) -> None:
        """When admin_api_key is empty, admin check should be skipped."""
        middleware = APIKeyAuthMiddleware(app=MagicMock())
        request = _make_request(
            path="/api/v1/query",
            api_key="some-key",
        )
        # Set admin key to empty
        from pydantic import SecretStr

        request.app.state.settings.admin_api_key = SecretStr("")

        mock_corpus = _make_corpus()

        with patch.object(
            middleware,
            "_find_corpus_by_key",
            new_callable=AsyncMock,
            return_value=mock_corpus,
        ):
            captured_state: dict = {}

            async def call_next(req: MagicMock) -> MagicMock:
                captured_state["is_admin"] = req.state.is_admin
                captured_state["corpus"] = req.state.corpus
                response = MagicMock()
                response.status_code = 200
                return response

            await middleware.dispatch(request, call_next)
            # Should NOT be admin, should resolve to corpus
            assert captured_state["is_admin"] is False
            assert captured_state["corpus"] is mock_corpus


# ---------------------------------------------------------------------------
# 10-11. get_authenticated_corpus dependency
# ---------------------------------------------------------------------------


class TestGetAuthenticatedCorpus:
    def test_get_authenticated_corpus_returns_corpus(self) -> None:
        """Should return corpus from request.state when corpus key used."""
        from callobuzz_rag.api.deps import get_authenticated_corpus

        mock_corpus = _make_corpus()
        request = MagicMock()
        request.state.is_admin = False
        request.state.corpus = mock_corpus

        result = get_authenticated_corpus(request)
        assert result is mock_corpus

    def test_get_authenticated_corpus_admin_raises_403(self) -> None:
        """Admin key should not work for corpus-scoped endpoints."""
        from fastapi import HTTPException

        from callobuzz_rag.api.deps import get_authenticated_corpus

        request = MagicMock()
        request.state.is_admin = True
        request.state.corpus = None

        with pytest.raises(HTTPException) as exc_info:
            get_authenticated_corpus(request)
        assert exc_info.value.status_code == 403

    def test_get_authenticated_corpus_no_auth_raises_401(self) -> None:
        """No auth state should raise 401."""
        from fastapi import HTTPException

        from callobuzz_rag.api.deps import get_authenticated_corpus

        request = MagicMock(spec=[])
        request.state = MagicMock(spec=[])

        with pytest.raises(HTTPException) as exc_info:
            get_authenticated_corpus(request)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 12-14. require_admin dependency
# ---------------------------------------------------------------------------


class TestRequireAdmin:
    def test_require_admin_passes_for_admin(self) -> None:
        """Admin key should pass require_admin without exception."""
        from callobuzz_rag.api.deps import require_admin

        request = MagicMock()
        request.state.is_admin = True

        # Should not raise
        require_admin(request)

    def test_require_admin_rejects_corpus_key(self) -> None:
        """Corpus key should be rejected by require_admin."""
        from fastapi import HTTPException

        from callobuzz_rag.api.deps import require_admin

        request = MagicMock()
        request.state.is_admin = False
        request.state.corpus = _make_corpus()

        with pytest.raises(HTTPException) as exc_info:
            require_admin(request)
        assert exc_info.value.status_code == 403

    def test_require_admin_rejects_no_auth(self) -> None:
        """No auth state should raise 401."""
        from fastapi import HTTPException

        from callobuzz_rag.api.deps import require_admin

        request = MagicMock(spec=[])
        request.state = MagicMock(spec=[])

        with pytest.raises(HTTPException) as exc_info:
            require_admin(request)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 15. Bcrypt timing sanity
# ---------------------------------------------------------------------------


class TestBcryptTiming:
    def test_bcrypt_timing_sanity_check(self) -> None:
        """Bcrypt verify should take measurable time (anti brute-force).

        This is a sanity check that verify_api_key actually calls bcrypt
        and doesn't accidentally do a plain string comparison.
        """
        from callobuzz_rag.core.secrets import hash_api_key, verify_api_key

        api_key = "test-api-key-for-timing-1234567890"
        hashed = hash_api_key(api_key)

        start = time.monotonic()
        for _ in range(5):
            verify_api_key(api_key, hashed)
        elapsed = time.monotonic() - start

        # 5 bcrypt verifies should take at least 50ms total (typically 200ms+)
        # If it's < 1ms, something is wrong (plain comparison)
        assert elapsed > 0.01, (
            f"Bcrypt verify too fast ({elapsed:.4f}s for 5 calls) — may not be using bcrypt"
        )
