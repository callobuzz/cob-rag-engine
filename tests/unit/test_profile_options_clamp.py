"""PIP-8: a request's `options` can narrow what a profile resolved, never widen it, when the
profile replaces the knowledge base's settings (Free, every downgrade)."""

import uuid

from callobuzz_rag.api.schemas.query import QueryOptions
from callobuzz_rag.conversation.surfaces import retrieval_kwargs
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver
from callobuzz_rag.tiers.resolve import resolve_with_profile
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig

WIDE = QueryOptions(top_k=100, final_k=30, enable_hyde=True, enable_classification=True)


def profile(**config: object) -> EffectiveProfile:
    return EffectiveProfile(
        id=uuid.uuid4(),
        name="P",
        label="P",
        config=ProfileConfig.model_validate(config),
        source="plan",
        available=True,
    )


def resolved(p: EffectiveProfile):  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    settings = Settings(_env_file=None, query_top_k=40, query_final_k=10, hyde_enabled=True)
    corpus = SimpleNamespace(llm_config={}, retrieval_config={})
    _, retrieval = resolve_with_profile(
        ConfigResolver(settings), corpus, headers=None, profile=p, settings=settings
    )
    return retrieval


FREE = profile(
    overrides_kb_settings=True,
    top_k=8,
    final_k=3,
    hyde_enabled=False,
    query_classification_enabled=False,
)


def test_a_request_cannot_widen_an_overriding_profile() -> None:
    kwargs = retrieval_kwargs(WIDE, resolved(FREE), profile=FREE)
    assert (kwargs["top_k"], kwargs["final_k"]) == (8, 3)
    assert kwargs["enable_hyde"] is False and kwargs["enable_classification"] is False


def test_a_request_may_still_narrow_it() -> None:
    narrow = QueryOptions(top_k=5, final_k=2)
    kwargs = retrieval_kwargs(narrow, resolved(FREE), profile=FREE)
    assert (kwargs["top_k"], kwargs["final_k"]) == (5, 2)


def test_a_normal_profile_leaves_the_options_as_they_were() -> None:
    standard = profile()
    kwargs = retrieval_kwargs(WIDE, resolved(standard), profile=standard)
    assert (kwargs["top_k"], kwargs["final_k"]) == (100, 30)
    assert kwargs["enable_hyde"] is True and kwargs["enable_classification"] is True
    again = retrieval_kwargs(WIDE, resolved(standard))  # no profile: as before
    assert again == kwargs
