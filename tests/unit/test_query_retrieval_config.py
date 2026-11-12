"""Tests for retrieval config flowing into query pipeline."""

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver


def _make_settings(**overrides) -> Settings:
    defaults = {
        "_env_file": None,
        "jina_api_key": "test",
        "llm_api_key": "test",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_query_pipeline_uses_corpus_retrieval_top_k():
    """Corpus retrieval_config.top_k should override engine default."""
    settings = _make_settings(query_top_k=20)
    resolver = ConfigResolver(settings)
    resolved = resolver.resolve_retrieval(corpus_retrieval_config={"top_k": 5})
    assert resolved.top_k == 5


def test_query_pipeline_uses_corpus_cache_disabled():
    """When corpus disables cache, resolved cache_enabled is False."""
    settings = _make_settings()
    resolver = ConfigResolver(settings)
    resolved = resolver.resolve_retrieval(corpus_retrieval_config={"cache_enabled": False})
    assert resolved.cache_enabled is False


def test_query_pipeline_corpus_mmr_overrides_engine():
    """Corpus retrieval_config.mmr_enabled overrides engine default."""
    settings = _make_settings(mmr_enabled=True)
    resolver = ConfigResolver(settings)
    resolved = resolver.resolve_retrieval(corpus_retrieval_config={"mmr_enabled": False})
    assert resolved.mmr_enabled is False


# --- Hybrid (dense + BM25) retrieval ------------------------------------------


def test_hybrid_search_defaults_on():
    """On since the BEIR baseline existed: better than dense on 5 of 6 datasets."""
    resolver = ConfigResolver(_make_settings())
    assert resolver.resolve_retrieval(corpus_retrieval_config=None).hybrid_search_enabled is True


def test_corpus_can_turn_hybrid_off():
    resolver = ConfigResolver(_make_settings())
    resolved = resolver.resolve_retrieval(corpus_retrieval_config={"hybrid_search_enabled": False})
    assert resolved.hybrid_search_enabled is False


def test_corpus_can_enable_hybrid_without_a_deploy():
    """A corpus can always set it explicitly."""
    resolver = ConfigResolver(_make_settings())
    resolved = resolver.resolve_retrieval(
        corpus_retrieval_config={"hybrid_search_enabled": True},
    )
    assert resolved.hybrid_search_enabled is True


def test_engine_default_can_be_flipped_once_there_is_evidence():
    resolver = ConfigResolver(_make_settings(hybrid_search_enabled=True))
    assert resolver.resolve_retrieval(corpus_retrieval_config=None).hybrid_search_enabled is True


def test_corpus_can_force_hybrid_off_against_an_enabled_engine():
    resolver = ConfigResolver(_make_settings(hybrid_search_enabled=True))
    resolved = resolver.resolve_retrieval(
        corpus_retrieval_config={"hybrid_search_enabled": False},
    )
    assert resolved.hybrid_search_enabled is False


# --- Pipeline ordering -------------------------------------------------------


def test_pipeline_order_defaults_off():
    """It changes both the ordering and the `score` number every caller sees.

    A cross-encoder score is not an RRF score, so this is a contract change as
    well as a ranking change — same reasoning as hybrid search, same default.
    """
    resolver = ConfigResolver(_make_settings())
    assert resolver.resolve_retrieval(corpus_retrieval_config=None).pipeline_order_enabled is False


def test_corpus_can_enable_pipeline_order_without_a_deploy():
    """A per-corpus flag lets a client application A/B the two orderings."""
    resolver = ConfigResolver(_make_settings())
    resolved = resolver.resolve_retrieval(
        corpus_retrieval_config={"pipeline_order_enabled": True},
    )
    assert resolved.pipeline_order_enabled is True


def test_corpus_can_force_pipeline_order_off_against_an_enabled_engine():
    resolver = ConfigResolver(_make_settings(pipeline_order_enabled=True))
    resolved = resolver.resolve_retrieval(
        corpus_retrieval_config={"pipeline_order_enabled": False},
    )
    assert resolved.pipeline_order_enabled is False


def test_search_mode_is_a_real_field():
    """It used to be sent by clients and silently discarded by extra='ignore'."""
    from callobuzz_rag.api.schemas.query import QueryOptions

    assert QueryOptions(search_mode="hybrid").search_mode == "hybrid"
    assert QueryOptions(search_mode="dense").search_mode == "dense"
    assert QueryOptions().search_mode is None


def test_an_unknown_search_mode_is_rejected_not_ignored():
    """Silence is precisely what let the original defect live undetected."""
    import pytest
    from pydantic import ValidationError

    from callobuzz_rag.api.schemas.query import QueryOptions

    with pytest.raises(ValidationError):
        QueryOptions(search_mode="magic")


# The keyword analyzer (identifiers, stopwords, plurals) is tested in test_sparse_vectors.py.


# --- Retrieval width vs answer size ---------------------------------------------


def test_final_k_is_resolvable_and_separate_from_top_k():
    """They are two numbers.

    Originally only `top_k` existed on the wire and `final_k` was read straight
    off global Settings, so no caller could influence how many chunks it got —
    asking for 10 returned 5 — while `top_k`, documented as "final chunks
    returned", actually narrowed SEARCH. Callers therefore set the width to their
    answer size and the rerank funnel collapsed to N-in-N-out.
    """
    resolver = ConfigResolver(_make_settings(query_top_k=20, query_final_k=5))
    resolved = resolver.resolve_retrieval(corpus_retrieval_config=None)
    assert (resolved.top_k, resolved.final_k) == (20, 5)


def test_corpus_can_set_answer_size_without_touching_search_width():
    resolver = ConfigResolver(_make_settings(query_top_k=20, query_final_k=5))
    resolved = resolver.resolve_retrieval(corpus_retrieval_config={"final_k": 8})
    assert resolved.final_k == 8
    assert resolved.top_k == 20, "raising the answer size must not narrow search"


def test_final_k_is_a_real_request_field():
    from callobuzz_rag.api.schemas.query import QueryOptions

    assert QueryOptions(final_k=8).final_k == 8
    assert QueryOptions().final_k is None


def test_no_preset_narrows_search_to_its_answer_size():
    """The regression that produced the live corpus's `{"top_k": 5}`.

    Every preset defined `top_k` alone and described it as the chunk count, so
    picking "economy" set the SEARCH width to 5. A preset may return few chunks;
    it must never search as narrowly as it answers, or reranking has nothing to
    choose between.
    """
    from callobuzz_rag.core.retrieval_presets import PRESETS, RETRIEVAL_FIELDS

    for name, preset in PRESETS.items():
        missing = [f for f in RETRIEVAL_FIELDS if f not in preset]
        assert not missing, f"preset {name!r} is missing {missing}"
        assert preset["top_k"] > preset["final_k"], (
            f"preset {name!r} searches {preset['top_k']} to return "
            f"{preset['final_k']} — the ranking stages get no candidates to cut"
        )


# --- rerank provider: local by default, per-corpus switch to the paid one -----
#
# This field decides whether the consumer is billed per query, so its default
# and its failure direction both matter more than the usual toggle.


def test_rerank_provider_defaults_to_the_free_local_one():
    resolver = ConfigResolver(_make_settings())
    resolved = resolver.resolve_retrieval(None)
    assert resolved.rerank_provider == "local"


def test_the_paid_reranker_model_is_pinned_not_inherited_from_the_caller():
    """`rerank_llm_model` used to default to empty, meaning "whatever model the
    caller is answering with". That is the wrong direction to fail in: switching
    a corpus to the paid reranker would then start ranking with whichever large
    model the consumer happened to be using, at that model's price, without
    anyone choosing it. Ranking is a judgement call, not reasoning — it is
    pinned to the engine default and a corpus can still
    override it.
    """
    resolver = ConfigResolver(_make_settings())
    resolved = resolver.resolve_retrieval(None)
    assert resolved.rerank_llm_model == "openai/gpt-oss-20b"

    overridden = resolver.resolve_retrieval({"rerank_llm_model": "openai/gpt-4o-mini"})
    assert overridden.rerank_llm_model == "openai/gpt-4o-mini"


def test_a_corpus_can_switch_to_the_paid_reranker_without_a_deploy():
    """The whole point: an admin toggles it, no restart, per corpus."""
    resolver = ConfigResolver(_make_settings())
    resolved = resolver.resolve_retrieval({"rerank_provider": "llm"})
    assert resolved.rerank_provider == "llm"


def test_a_corpus_can_force_local_against_an_llm_engine_default():
    resolver = ConfigResolver(_make_settings(rerank_provider="llm"))
    resolved = resolver.resolve_retrieval({"rerank_provider": "local"})
    assert resolved.rerank_provider == "local"


def test_an_unknown_provider_falls_back_to_local_not_to_spending():
    """An unreadable value must land on the free side of the decision.

    The corpus row is written by an admin UI and by the auto-optimizer, so a
    junk value is reachable. Resolving it to the paid provider would bill the
    consumer for a typo.
    """
    resolver = ConfigResolver(_make_settings())
    for bad in ("hosted", "LLM ", "", None, 7, True):
        resolved = resolver.resolve_retrieval({"rerank_provider": bad})
        assert resolved.rerank_provider == "local", f"{bad!r} must not resolve to paid"


def test_provider_is_case_insensitive():
    resolver = ConfigResolver(_make_settings())
    assert resolver.resolve_retrieval({"rerank_provider": "LLM"}).rerank_provider == "llm"


def test_rerank_model_resolves_corpus_over_engine():
    resolver = ConfigResolver(_make_settings(rerank_llm_model="engine/default"))
    assert (
        resolver.resolve_retrieval({"rerank_llm_model": "corpus/choice"}).rerank_llm_model
        == "corpus/choice"
    )
    assert resolver.resolve_retrieval(None).rerank_llm_model == "engine/default"


def test_an_empty_rerank_model_means_use_the_callers_cheap_model():
    """Empty string and unset must not become the literal model name ""."""
    resolver = ConfigResolver(_make_settings(rerank_llm_model=""))
    assert resolver.resolve_retrieval({"rerank_llm_model": ""}).rerank_llm_model is None
    assert resolver.resolve_retrieval(None).rerank_llm_model is None


def test_an_unknown_provider_on_the_corpus_input_schema_is_a_422():
    """Same lesson as `search_mode`: a typo must be loud, not silently dropped."""
    import pydantic
    import pytest

    from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput

    assert RetrievalConfigInput(rerank_provider="llm").rerank_provider == "llm"
    with pytest.raises(pydantic.ValidationError):
        RetrievalConfigInput(rerank_provider="hosted")
