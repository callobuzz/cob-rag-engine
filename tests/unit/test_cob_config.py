"""COB's fixed assistant config: valid, named COB, no leads, no handoff, no operator input."""

from callobuzz_rag.cob.config import (
    COB_CONFIG_VERSION,
    admin_managed_texts,
    cob_config,
    cob_runtime,
    cob_text,
    generic_starters,
)
from callobuzz_rag.cob.manifest import docs_root, load_manifest


def test_cob_config_is_valid_and_fixed() -> None:
    cfg = cob_config()
    assert cfg.identity.assistant_name == "COB"
    assert not cfg.leads.enabled and not cfg.handoff.enabled
    assert cfg.language.reply_policy == "mirror"
    assert set(cfg.language.supported) == {"en", "hi", "hi-Latn"}
    assert cfg.scope.scope_mode == "docs_plus_domain"
    assert {"en", "hi", "hi-Latn"} <= set(admin_managed_texts())


def test_topics_are_the_manifest_topics() -> None:
    manifest_topics = load_manifest(docs_root()).topic_ids()
    assert {t.id for t in cob_config().scope.topics} == set(manifest_topics)


def test_runtime_is_enabled_version_one() -> None:
    runtime = cob_runtime()
    assert runtime.version == COB_CONFIG_VERSION == 1
    assert runtime.enabled and runtime.draft_revision is None
    assert runtime.config is cob_config()


def test_texts_exist_in_every_language() -> None:
    for key in ("search_cap", "search_unavailable", "search_none", "open_link", "open_docs"):
        for lang in ("en", "hi", "hi-Latn"):
            assert cob_text(key, lang)
    assert "{date}" in cob_text("search_cap", "en")
    for lang in ("en", "hi", "hi-Latn"):
        assert len(generic_starters(lang)) == 4


def test_unknown_language_falls_back_to_english() -> None:
    assert cob_text("open_link", "ta") == cob_text("open_link", "en")
    assert generic_starters("ta") == generic_starters("en")


def test_a_customer_role_has_no_administrator_only_topic_in_scope() -> None:
    from callobuzz_rag.cob.config import admin_only_topic_ids, cob_config_for, cob_runtime_for
    from callobuzz_rag.cob.types import CobRole

    hidden = admin_only_topic_ids()
    assert {"settings", "operations"} <= hidden
    full = {t.id for t in cob_config().scope.topics}
    for role in (CobRole.OWNER, CobRole.MEMBER, CobRole.PUBLIC):
        ids = {t.id for t in cob_config_for(role).scope.topics}
        assert not ids & hidden and ids == full - hidden
        assert cob_runtime_for(role).config is cob_config_for(role)
    for role in (CobRole.ADMIN, CobRole.OPERATOR_OWNER):
        assert cob_config_for(role) is cob_config()
    assert {t.id for t in cob_config().scope.topics} == full  # the shared config is untouched
