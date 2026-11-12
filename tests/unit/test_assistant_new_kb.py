"""POST /api/v1/corpora publishes v1 from the Customer support template."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.api.routes.corpus import publish_initial_assistant
from callobuzz_rag.core.config import Settings

STORE = "callobuzz_rag.api.routes.corpus.AssistantConfigStore"


def _request(settings: Settings) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings, vector_store=object())),
        state=SimpleNamespace(is_admin=True),
        client=None,
        headers={},
    )


def _corpus(prompt: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        id="c1", tenant_id="t1", prompt_config=prompt, qdrant_collection="corpus_c1"
    )


@pytest.mark.parametrize("on", [True, False])
async def test_initial_assistant(on: bool) -> None:
    settings = Settings(_env_file=None, assistant_default_for_new_kbs=on, encryption_key="k")
    session = AsyncMock()
    with patch(STORE) as store:
        store.return_value.publish_initial = AsyncMock()
        store.return_value.pending_card = None
        await publish_initial_assistant(_request(settings), session, _corpus())
    config = store.return_value.publish_initial.await_args.args[1]
    assert config.enabled is on and config.identity.business_type == "generic_support"
    assert config.leads.enabled is True
    session.commit.assert_awaited()


async def test_leads_off_without_an_encryption_key() -> None:
    settings = Settings(_env_file=None, encryption_key="")
    with patch(STORE) as store:
        store.return_value.publish_initial = AsyncMock()
        store.return_value.pending_card = None
        await publish_initial_assistant(_request(settings), AsyncMock(), _corpus())
    assert store.return_value.publish_initial.await_args.args[1].leads.enabled is False


async def test_the_legacy_prompt_config_is_carried_over() -> None:
    settings = Settings(_env_file=None, encryption_key="k")
    with patch(STORE) as store:
        store.return_value.publish_initial = AsyncMock()
        store.return_value.pending_card = None
        await publish_initial_assistant(
            _request(settings),
            AsyncMock(),
            _corpus({"system_prompt": "Answer as the Shree Labs desk."}),
        )
    config = store.return_value.publish_initial.await_args.args[1]
    assert config.advanced.extra_instructions == "Answer as the Shree Labs desk."


async def test_failure_never_fails_kb_creation() -> None:
    settings = Settings(_env_file=None)
    session = AsyncMock()
    corpus = _corpus()
    with patch(STORE) as store:
        store.return_value.publish_initial = AsyncMock(side_effect=RuntimeError("db hiccup"))
        await publish_initial_assistant(_request(settings), session, corpus)
    session.rollback.assert_awaited()
    session.refresh.assert_awaited_with(corpus)  # a rollback expires the corpus


async def test_a_request_without_settings_is_survived_too() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), state=SimpleNamespace())
    session = AsyncMock()
    await publish_initial_assistant(request, session, _corpus())  # type: ignore[arg-type]
    session.rollback.assert_awaited()


async def test_a_card_to_index_is_dispatched_after_the_commit() -> None:
    settings = Settings(_env_file=None, encryption_key="k")
    pending = object()
    calls: list[str] = []
    session = AsyncMock()
    session.commit = AsyncMock(side_effect=lambda: calls.append("commit"))

    async def dispatch(*_args: Any) -> None:
        calls.append("dispatch")

    with (
        patch(STORE) as store,
        patch("callobuzz_rag.api.routes.corpus.dispatch_business_card", dispatch),
    ):
        store.return_value.publish_initial = AsyncMock()
        store.return_value.pending_card = pending
        await publish_initial_assistant(_request(settings), session, _corpus())
    assert calls == ["commit", "dispatch"]
