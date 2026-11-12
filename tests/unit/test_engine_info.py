"""GET /api/v1/info: features, models and limits for a UI, no secrets (G-12)."""

import pytest
from pydantic import SecretStr

from callobuzz_rag.api.routes.ingest import ALLOWED_TYPES, EXTENSION_MIME_TYPES
from callobuzz_rag.transcription.base import AUDIO_MIME_TYPES
from tests.unit.scope_harness import ADMIN_KEY, call, scoped_app


@pytest.mark.parametrize(
    ("key", "status"), [("admin", 200), ("tk_a", 200), ("ck_a", 200), (None, 401)]
)
async def test_any_key(key, status) -> None:
    with scoped_app() as (app, _):
        res = await call(app, key, "GET", "/api/v1/info")
    assert res.status_code == status


async def test_reports_features_models_and_limits_without_secrets() -> None:
    with scoped_app() as (app, _):
        s = app.state.settings
        s.llm_api_key = SecretStr("sk-very-secret-llm-key")
        s.public_url = "https://rag.example.com"
        s.enable_content_portal = True
        s.transcription_provider = "off"
        app.state.reranker = object()
        res = await call(app, "ck_a", "GET", "/api/v1/info")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "sk-very-secret-llm-key" not in res.text and ADMIN_KEY not in res.text
    assert body["version"]
    assert body["features"]["content_portal"] is True
    assert body["features"]["streaming"] is True
    assert body["features"]["transcription"] == {"enabled": False, "provider": "off"}
    assert body["features"]["reranker"]["local"] is True
    assert body["features"]["accounts"] is False  # no RAG_SESSION_SECRET
    assert body["llm"]["key_configured"] is True
    assert body["embedding"]["model"] == s.embedding_model
    assert body["public_url"] == "https://rag.example.com" and body["public_url_configured"]
    limits = body["limits"]
    assert limits["max_upload_bytes"] == s.max_upload_bytes == 50 * 1024 * 1024
    assert limits["max_document_tokens"] == s.max_document_tokens == 500_000
    assert "max_upload_tokens" not in limits
    assert "application/pdf" in limits["allowed_mime_types"]
    assert not set(limits["allowed_mime_types"]) & AUDIO_MIME_TYPES
    assert ".pdf" in limits["allowed_extensions"] and ".mp3" not in limits["allowed_extensions"]
    assert limits["max_query_chars"] == 2000
    assert limits["max_records_per_request"] == 1000


async def test_audio_types_appear_with_transcription() -> None:
    with scoped_app() as (app, _):
        app.state.settings.transcription_provider = "whisper_api"
        app.state.reranker = None
        res = await call(app, "admin", "GET", "/api/v1/info")
    body = res.json()
    assert body["features"]["transcription"]["enabled"] is True
    assert body["features"]["reranker"]["local"] is False
    assert "audio/mpeg" in body["limits"]["allowed_mime_types"]
    assert ".mp3" in body["limits"]["allowed_extensions"]
    assert body["public_url_configured"] is False  # falls back to the request's own address


def test_every_known_extension_maps_to_an_accepted_type() -> None:
    assert set(EXTENSION_MIME_TYPES.values()) <= set(ALLOWED_TYPES) | set(AUDIO_MIME_TYPES)


def test_retrieval_defaults_cover_every_corpus_retrieval_field() -> None:
    from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput
    from callobuzz_rag.api.schemas.identity import RetrievalDefaultsInfo

    corpus_fields = set(RetrievalConfigInput.model_fields) - {"preset"}
    assert set(RetrievalDefaultsInfo.model_fields) == corpus_fields


def test_each_retrieval_default_is_what_the_resolver_uses_for_an_empty_corpus() -> None:
    from dataclasses import asdict

    from callobuzz_rag.api.routes.identity import retrieval_defaults
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.core.config_resolver import ConfigResolver

    settings = Settings()
    reported = retrieval_defaults(settings).model_dump()
    resolved = asdict(ConfigResolver(settings).resolve_retrieval({}))
    assert reported.keys() <= resolved.keys()
    for name, value in reported.items():
        assert value == resolved[name], name
    # The fields the bug was about, pinned to the engine's own settings.
    assert reported["hybrid_search_enabled"] is settings.hybrid_search_enabled
    assert reported["fusion_strategy"] == settings.fusion_strategy
    assert reported["hybrid_dense_weight"] == settings.hybrid_dense_weight
    assert reported["rrf_k"] == settings.rrf_k


async def test_info_reports_the_engine_retrieval_defaults() -> None:
    with scoped_app() as (app, _):
        s = app.state.settings
        s.hybrid_search_enabled = True
        s.fusion_strategy = "rrf"
        s.hybrid_dense_weight = 0.6
        s.rrf_k = 42
        s.rerank_provider = "llm"
        res = await call(app, "tk_a", "GET", "/api/v1/info")
    assert res.status_code == 200, res.text
    defaults = res.json()["retrieval_defaults"]
    assert defaults["hybrid_search_enabled"] is True
    assert defaults["fusion_strategy"] == "rrf"
    assert defaults["hybrid_dense_weight"] == 0.6
    assert defaults["rrf_k"] == 42
    assert defaults["rerank_provider"] == "llm"
    assert defaults["cache_enabled"] is True


async def test_accounts_feature_follows_the_session_secret() -> None:
    secret = "session-secret-" + "x" * 32
    with scoped_app() as (app, _):
        app.state.settings.session_secret = SecretStr(secret)
        res = await call(app, "admin", "GET", "/api/v1/info")
    assert res.json()["features"]["accounts"] is True
    assert secret not in res.text


async def test_reports_cob_for_the_dashboard() -> None:
    with scoped_app() as (app, _):
        res = await call(app, "tk_a", "GET", "/api/v1/info")
        off = app.state.settings.model_copy(update={"cob_enabled": False})
        app.state.settings = off
        res_off = await call(app, "tk_a", "GET", "/api/v1/info")
    cob = res.json()["features"]["cob"]
    assert cob == {"enabled": True, "public": True, "corpus_id": None, "docs_version": None}
    cob_off = res_off.json()["features"]["cob"]
    assert cob_off["enabled"] is False and cob_off["public"] is False


# BIL2 billing
async def test_billing_feature_follows_the_switch() -> None:
    with scoped_app() as (app, _):
        on = await call(app, "admin", "GET", "/api/v1/info")
        app.state.settings.billing_enabled = False
        off = await call(app, "admin", "GET", "/api/v1/info")
    assert on.json()["features"]["billing"] == {"enabled": True}
    assert off.json()["features"]["billing"] == {"enabled": False}


# /BIL2 billing


# TV1 human
async def test_info_tells_a_dashboard_whether_the_human_button_may_be_hidden() -> None:
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.accounts.settings_store import EngineSettings

    for allowed in (False, True):
        settings = EngineSettings.model_validate({"trust": {"allow_hide_human_button": allowed}})
        with (
            scoped_app() as (app, _),
            patch(
                "callobuzz_rag.accounts.settings_store.get_engine_settings",
                AsyncMock(return_value=settings),
            ),
        ):
            res = await call(app, "tk_a", "GET", "/api/v1/info")
        assert res.status_code == 200, res.text
        trust = res.json()["features"]["trust"]  # TV5: the disclosure switches ride along
        assert trust["allow_hide_human_button"] is allowed
        assert trust["require_ai_disclosure"] is True
        assert trust["disclosure_wording_editable"] is True


async def test_info_keeps_the_button_when_the_engine_setting_cannot_be_read() -> None:
    from unittest.mock import AsyncMock, patch

    with (
        scoped_app() as (app, _),
        patch(
            "callobuzz_rag.accounts.settings_store.get_engine_settings",
            AsyncMock(side_effect=RuntimeError("down")),
        ),
    ):
        res = await call(app, "tk_a", "GET", "/api/v1/info")
    assert res.status_code == 200
    assert res.json()["features"]["trust"]["allow_hide_human_button"] is False
    assert res.json()["features"]["trust"]["require_ai_disclosure"] is True  # TV5


# /TV1 human
