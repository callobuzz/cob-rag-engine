"""Settings, per-knowledge-base switches and who may flip them."""

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.scope_harness import ADMIN_KEY, scoped_app

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def test_engine_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.boilerplate_strip_enabled is True
    assert s.boilerplate_min_docs == 3
    assert s.boilerplate_max_chars == 600
    assert s.similarity_min_score == 0.90
    assert s.collapse_duplicates is True


def test_every_new_setting_is_in_env_example() -> None:
    text = ENV_EXAMPLE.read_text("utf-8")
    for key in (
        "RAG_BOILERPLATE_STRIP_ENABLED=true",
        "RAG_BOILERPLATE_MIN_DOCS=3",
        "RAG_BOILERPLATE_MAX_CHARS=600",
        "RAG_SIMILARITY_MIN_SCORE=0.90",
        "RAG_COLLAPSE_DUPLICATES=true",
    ):
        assert key in text, key


def test_the_env_example_values_load() -> None:
    values = dict(
        line.split("=", 1)
        for line in ENV_EXAMPLE.read_text("utf-8").splitlines()
        if line.startswith(("RAG_BOILERPLATE", "RAG_SIMILARITY", "RAG_COLLAPSE"))
    )
    s = Settings(
        _env_file=None,
        boilerplate_strip_enabled=values["RAG_BOILERPLATE_STRIP_ENABLED"],
        boilerplate_min_docs=values["RAG_BOILERPLATE_MIN_DOCS"],
        boilerplate_max_chars=values["RAG_BOILERPLATE_MAX_CHARS"],
        similarity_min_score=values["RAG_SIMILARITY_MIN_SCORE"],
        collapse_duplicates=values["RAG_COLLAPSE_DUPLICATES"],
    )
    assert s.boilerplate_min_docs == 3 and s.similarity_min_score == 0.9


def test_a_knowledge_base_can_switch_the_collapse_off() -> None:
    resolver = ConfigResolver(Settings(_env_file=None))
    assert resolver.resolve_retrieval(None).collapse_duplicates is True
    assert resolver.resolve_retrieval({"collapse_duplicates": False}).collapse_duplicates is False
    off = ConfigResolver(Settings(_env_file=None, collapse_duplicates=False))
    assert off.resolve_retrieval(None).collapse_duplicates is False
    assert off.resolve_retrieval({"collapse_duplicates": True}).collapse_duplicates is True


def test_page_stripping_resolves_from_the_engine_setting_and_the_knowledge_base() -> None:
    on = ConfigResolver(Settings(_env_file=None))
    assert on.resolve_retrieval(None).boilerplate_strip is True
    assert on.resolve_retrieval({"boilerplate_strip": False}).boilerplate_strip is False
    off = ConfigResolver(Settings(_env_file=None, boilerplate_strip_enabled=False))
    assert off.resolve_retrieval(None).boilerplate_strip is False


def test_the_retrieval_config_accepts_the_two_switches_and_rejects_typos() -> None:
    body = RetrievalConfigInput(boilerplate_strip=False, collapse_duplicates=False)
    assert body.model_dump(exclude_unset=True) == {
        "boilerplate_strip": False,
        "collapse_duplicates": False,
    }
    with pytest.raises(ValueError):
        RetrievalConfigInput(boilerplate_stripping=False)  # type: ignore[call-arg]


async def _call(key: str, method: str, path_template: str, body: dict[str, Any]) -> Any:
    with scoped_app() as (app, world):
        app.state.account_rate_limiter = AccountRateLimiter(client=FakeRedis())
        path = path_template.format(corpus_id=world.corpus_a.id)
        headers = {"X-API-Key": ADMIN_KEY if key == "admin" else key}
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.request(method, path, headers=headers, json=body)


ROUTES = [
    ("PUT", "/api/v1/corpora/{corpus_id}", "retrieval_config"),
    ("PATCH", "/api/v1/config?corpus_id={corpus_id}", "retrieval_config"),
]


@pytest.mark.parametrize(("method", "path", "field"), ROUTES)
async def test_a_member_cannot_switch_page_stripping(method: str, path: str, field: str) -> None:
    res = await _call("member_a", method, path, {field: {"boilerplate_strip": False}})
    assert res.status_code == 403, res.text
    assert res.json()["code"] == "owner_required"


@pytest.mark.parametrize(("method", "path", "field"), ROUTES)
async def test_a_member_still_edits_other_retrieval_settings(
    method: str, path: str, field: str
) -> None:
    res = await _call(
        "member_a", method, path, {field: {"top_k": 12, "collapse_duplicates": False}}
    )
    assert res.status_code != 403, res.text


@pytest.mark.parametrize("key", ["owner_a", "tk_a", "admin"])
@pytest.mark.parametrize(("method", "path", "field"), ROUTES)
async def test_owners_and_admin_may_switch_page_stripping(
    key: str, method: str, path: str, field: str
) -> None:
    res = await _call(key, method, path, {field: {"boilerplate_strip": False}})
    assert res.status_code != 403, res.text
