"""P3 T6: the widget's privacy link and the lead consent link fall back to the deployer's
published Privacy notice; the business's own policy always wins."""

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.settings_store import EngineSettings
from callobuzz_rag.api.app import create_app
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.conversation.leads.consent import notice_for
from callobuzz_rag.conversation.store import RuntimeConfig
from callobuzz_rag.db.models.legal import LegalDocument
from callobuzz_rag.legal import privacy_link
from callobuzz_rag.widget.security import LimitResult
from tests.unit.accounts_fakes import FakeDB
from tests.unit.conversation_helpers import make_config
from tests.unit.test_widget import SITE, _client, _key, _session, _settings, _use_session

pytestmark = [pytest.mark.usefixtures("unlimited_plan")]

OWN = "https://shree-labs.in/privacy"
DEPLOYER = "https://rag.example.com/legal/privacy"


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    privacy_link.reset()
    yield
    privacy_link.reset()


def cache_deployer_url(url: str | None) -> None:
    """What `ensure` would have stored, fresh for a long time (the mock session cannot answer)."""
    privacy_link._published = url is not None
    privacy_link._consent_url = url
    privacy_link._expires = time.monotonic() + 3600


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
def app() -> Any:
    application = create_app()
    application.state.settings = _settings()
    application.state.widget_rate_limiter = SimpleNamespace(
        check=AsyncMock(return_value=LimitResult(True))
    )
    return application


def _runtime(policy_url: str | None) -> RuntimeConfig:
    cfg = make_config(leads={"consent": {"privacy_policy_url": policy_url}})
    return RuntimeConfig(config=cfg, version=3, draft_revision=None, enabled=True)


async def _config(app: Any, corpus: Any, runtime: Any) -> dict[str, Any]:
    _use_session(app, _session(_key(corpus.id), corpus))
    with (
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch(
            "callobuzz_rag.accounts.settings_store.get_engine_settings",
            AsyncMock(return_value=EngineSettings()),
        ),
    ):
        async with await _client(app) as c:
            res = await c.get(
                "/api/v1/widget/config", headers={"X-Widget-Key": "pk_x", "Origin": SITE}
            )
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


async def test_the_businesss_own_policy_wins(app: Any, corpus: Any) -> None:
    cache_deployer_url(DEPLOYER)
    assert (await _config(app, corpus, _runtime(OWN)))["privacy_url"] == OWN


async def test_the_deployers_notice_is_the_fallback(app: Any, corpus: Any) -> None:
    cache_deployer_url(DEPLOYER)
    assert (await _config(app, corpus, _runtime(None)))["privacy_url"] == DEPLOYER


async def test_the_fallback_does_not_need_the_assistant_to_be_on(app: Any, corpus: Any) -> None:
    cache_deployer_url(DEPLOYER)
    assert (await _config(app, corpus, None))["privacy_url"] == DEPLOYER


async def test_neither_means_no_link(app: Any, corpus: Any) -> None:
    cache_deployer_url(None)
    assert (await _config(app, corpus, _runtime(None)))["privacy_url"] is None
    assert (await _config(app, corpus, None))["privacy_url"] is None


# ------------------------------------------------------------------ the lead consent notice


def test_consent_link_follows_the_same_fallback() -> None:
    plain = notice_for(make_config(), "en")
    assert "Privacy policy" not in plain
    with_deployer = notice_for(make_config().with_deployer_privacy_url(DEPLOYER), "en")
    assert with_deployer == plain + f" [Privacy policy]({DEPLOYER})"
    own_cfg = make_config(leads={"consent": {"privacy_policy_url": OWN}})
    own = notice_for(own_cfg.with_deployer_privacy_url(DEPLOYER), "en")
    assert own == plain + f" [Privacy policy]({OWN})"  # the business's own, never the deployer's


def test_consent_link_is_translated_with_the_notice() -> None:
    cfg = make_config().with_deployer_privacy_url(DEPLOYER)
    assert "[गोपनीयता नीति](" + DEPLOYER + ")" in notice_for(cfg, "hi")


# ------------------------------------------------------------------ the cache


def _privacy(version: int) -> LegalDocument:
    when = datetime(2026, 10, 8, tzinfo=UTC)
    return LegalDocument(
        slug="privacy",
        version=version,
        title="Privacy Notice",
        body_md="x",
        revision=1,
        published_at=when,
        created_at=when,
        updated_at=when,
    )


async def test_ensure_reads_the_published_notice_and_caches_it() -> None:
    db = FakeDB()
    session = db.session()
    assert await privacy_link.ensure(session, "https://rag.example.com/") is False
    db.add(_privacy(1))
    # still cached: a second read inside the window asks nothing
    assert await privacy_link.ensure(session, "https://rag.example.com/") is False
    privacy_link._expires = 0.0
    assert await privacy_link.ensure(session, "https://rag.example.com/") is True
    assert privacy_link.consent_url() == DEPLOYER
    assert privacy_link.page_url("https://other.example/") == "https://other.example/legal/privacy"


async def test_a_request_derived_host_never_enters_the_cache() -> None:
    """With no RAG_PUBLIC_URL the consent address is None: a Host header is not shared state."""
    db = FakeDB()
    db.add(_privacy(1))
    assert await privacy_link.ensure(db.session(), "") is True
    assert privacy_link.published() is True
    assert privacy_link.consent_url() is None


class _Nested:
    entered = 0
    exited_with: Any = None

    async def __aenter__(self) -> "_Nested":
        type(self).entered += 1
        return self

    async def __aexit__(self, exc_type: Any, *args: Any) -> bool:
        type(self).exited_with = exc_type
        return False


def _broken_session() -> Any:
    return SimpleNamespace(
        begin_nested=lambda: _Nested(), execute=AsyncMock(side_effect=RuntimeError("db down"))
    )


async def test_a_failed_read_keeps_the_last_known_state() -> None:
    cache_deployer_url(DEPLOYER)
    privacy_link._expires = 0.0
    assert await privacy_link.ensure(_broken_session(), "https://rag.example.com") is True
    assert privacy_link.consent_url() == DEPLOYER


async def test_the_probe_runs_in_a_savepoint() -> None:
    """A failed statement rolls back to the savepoint, not the request's own transaction."""
    _Nested.entered, _Nested.exited_with = 0, None
    assert await privacy_link.ensure(_broken_session(), "https://x") is False
    assert _Nested.entered == 1 and _Nested.exited_with is RuntimeError


async def test_a_session_that_cannot_answer_is_no_link() -> None:
    session = SimpleNamespace(
        begin_nested=lambda: _Nested(),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: object())),
    )
    assert await privacy_link.ensure(session, "https://x") is False  # type: ignore[arg-type]


async def test_every_surface_records_the_same_consent_text() -> None:
    """Whichever process answers (a warm API process, a cold worker), the address comes from
    `get_runtime_config`, so the stored consent wording is byte-identical."""
    from callobuzz_rag.conversation.store import get_runtime_config

    cfg = make_config()
    row = SimpleNamespace(status="published", version=1, draft_revision=None, config={})
    session = SimpleNamespace(
        begin_nested=lambda: _Nested(),
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [row]),
                scalar_one_or_none=lambda: 1,
            )
        ),
    )
    texts = []
    with patch("callobuzz_rag.conversation.store.row_config", lambda r: cfg.model_copy(deep=True)):
        for _ in range(2):
            privacy_link.reset()  # a process that has never served a widget request
            runtime = await get_runtime_config(session, uuid.uuid4())  # type: ignore[arg-type]
            assert runtime is not None
            texts.append(notice_for(runtime.config, "en"))
            assert "deployer_privacy_url" not in runtime.config.model_dump()
    assert texts[0] == texts[1]


async def test_a_body_cannot_set_the_deployer_address() -> None:
    from pydantic import ValidationError

    from callobuzz_rag.conversation.schema import AssistantConfig

    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"deployer_privacy_url": "https://evil.example"})


async def test_published_all_asks_for_one_row_per_page_without_bodies() -> None:
    from sqlalchemy.dialects import postgresql

    from callobuzz_rag.legal.store import published_all

    seen: list[Any] = []

    async def execute(stmt: Any) -> Any:
        seen.append(stmt)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    assert await published_all(SimpleNamespace(execute=execute)) == []  # type: ignore[arg-type]
    sql = str(seen[0].compile(dialect=postgresql.dialect()))
    assert "DISTINCT ON (legal_documents.slug)" in sql
    assert "body_html" not in sql and "body_md" not in sql and "body_filled" not in sql
