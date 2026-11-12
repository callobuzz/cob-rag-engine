"""Is a knowledge base's embedding model multilingual? (design section 11: recommend a
multilingual model when the owner chooses Hindi or Hinglish and the current one is not).

The answer is the catalog's `multilingual` flag, set per entry from each provider's model
documentation when the catalog is edited. A model that is not in the catalog is reported as not
multilingual: guessing from the name would put a recommendation in front of an owner that
nobody checked.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings


def multilingual_flag(applied: dict[str, Any] | None, settings: "Settings") -> bool | None:
    """The catalog flag of the model a knowledge base embeds with: True, False, or None when
    the catalog has no entry or its documentation does not say (no recommendation either way).

    `applied` is the corpus's `applied_embedding` ({"provider", "model", "dimensions"}); it can
    be NULL on old rows, in which case the engine default applies. The same model string sits
    under several providers in the catalog, so the provider is part of the lookup.
    """
    from callobuzz_rag.core.model_catalog import get_embedding_catalog_entry

    model = (applied or {}).get("model") or settings.embedding_model
    provider = (applied or {}).get("provider") or settings.embedding_provider
    if not isinstance(model, str) or not model:
        return None
    entry = get_embedding_catalog_entry(model, provider if isinstance(provider, str) else None)
    flag = entry.get("multilingual") if entry else None
    return flag if isinstance(flag, bool) else None


def is_multilingual(applied: dict[str, Any] | None, settings: "Settings") -> bool:
    """True only when the catalog says so; an unknown model is never guessed from its name."""
    return multilingual_flag(applied, settings) is True
