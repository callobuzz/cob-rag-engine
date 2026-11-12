"""COB's fixed assistant configuration and its canned texts.

`data/cob_assistant.json` holds the `AssistantConfig` COB answers with (name "COB", no leads,
no handoff, replies mirror the person's language) and a `texts` block of the sentences COB says
itself (administrator-managed, search mode, link labels, starters). Nothing here reads an
operator or customer setting: COB's name and manner are not configurable.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from callobuzz_rag.cob.types import ALLOWED_AUDIENCES, Audience, CobRole
from callobuzz_rag.conversation.schema import AssistantConfig, load_config
from callobuzz_rag.conversation.store import RuntimeConfig

COB_CONFIG_VERSION: Final = 1
DATA_PATH: Final = Path(__file__).parent / "data" / "cob_assistant.json"
_LANGS: Final = ("en", "hi", "hi-Latn")


@lru_cache(maxsize=1)
def _raw() -> dict[str, Any]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@lru_cache(maxsize=1)
def cob_config() -> AssistantConfig:
    return load_config(dict(_raw()["assistant"]))


@lru_cache(maxsize=1)
def cob_runtime() -> RuntimeConfig:
    return RuntimeConfig(
        config=cob_config(), version=COB_CONFIG_VERSION, draft_revision=None, enabled=True
    )


def _lang(language: str) -> str:
    return language if language in _LANGS else "en"


def cob_text(key: str, language: str, **values: str) -> str:
    """One of COB's own sentences in `language` (English for any other), placeholders filled."""
    text = str(_raw()["texts"][key][_lang(language)])
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


def admin_managed_texts() -> dict[str, str]:
    return dict(_raw()["texts"]["admin_managed"])


def generic_starters(language: str) -> list[str]:
    return list(_raw()["texts"]["generic_starters"][_lang(language)])


@lru_cache(maxsize=1)
def admin_only_topic_ids() -> frozenset[str]:
    """Topics the manifest marks administrator-only (server settings, operating the engine)."""
    from callobuzz_rag.cob.manifest import load_manifest

    return frozenset(t.id for t in load_manifest().topics if t.audience == Audience.ADMIN)


@lru_cache(maxsize=len(CobRole))
def cob_config_for(role: CobRole) -> AssistantConfig:
    """COB's configuration for `role`. A role that cannot read administrator pages does not
    get the administrator-only topics in scope, so a server question is neither on-topic for
    the labeller nor described in its prompt."""
    config = cob_config()
    if Audience.ADMIN in ALLOWED_AUDIENCES[role]:
        return config
    hidden = admin_only_topic_ids()
    topics = [t for t in config.scope.topics if t.id not in hidden]
    return config.model_copy(update={"scope": config.scope.model_copy(update={"topics": topics})})


@lru_cache(maxsize=len(CobRole))
def cob_runtime_for(role: CobRole) -> RuntimeConfig:
    return RuntimeConfig(
        config=cob_config_for(role), version=COB_CONFIG_VERSION, draft_revision=None, enabled=True
    )
