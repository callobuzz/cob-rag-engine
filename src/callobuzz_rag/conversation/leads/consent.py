"""The consent notice a visitor sees before the first lead field, and its record.

Defaults follow the DPDP Act 2023 / Rules 2025 notice elements: what is collected, the
purpose, who sees it, how long it is kept, how to withdraw, a contact and the privacy
policy. The texts live in `conversation/schema.py` (`_NOTICE`, the default of
`leads.consent.notice`); owners edit them per language. They are a starting point, not
legal advice: the owner is responsible for their own policy.
"""

import hashlib
import unicodedata
import uuid
from typing import TYPE_CHECKING, Any

from callobuzz_rag.conversation.schema import (  # TV2 reference
    _NOTICE,
    _NOTICE_V1,
    NOTICE_VERSION,
    fill_persona,
)
from callobuzz_rag.db.models.lead_consent import LeadConsent as LeadConsentRow

if TYPE_CHECKING:
    from callobuzz_rag.conversation.schema import AssistantConfig

DEFAULT_NOTICES: dict[str, str] = {k: v for k, v in _NOTICE.items()}

_LABELS = {
    "en": "I agree to share my details for this purpose.",
    "hi": "मैं इस उद्देश्य के लिए अपनी जानकारी साझा करने के लिए सहमत हूँ।",
    "hi-Latn": "Main is purpose ke liye apni details share karne ke liye sahmat hoon.",
}
_POLICY = {"en": "Privacy policy", "hi": "गोपनीयता नीति", "hi-Latn": "Privacy policy"}
_NO_CONTACT = {"en": "the business", "hi": "व्यवसाय", "hi-Latn": "business"}


def _lang(language: str, table: dict[str, str]) -> str:
    return language if language in table else "en"


# TV2 reference
def _plain(table: dict[Any, str]) -> dict[str, str]:
    return {str(k): v for k, v in table.items()}


def _is_v1_default(config: "AssistantConfig", lang: str) -> bool:
    """True when the stored wording for `lang` is the untouched first default text."""
    stored = _plain(config.leads.consent.notice).get(lang)
    return stored is not None and stored == _plain(_NOTICE_V1).get(lang)


def notice_version_for(config: "AssistantConfig", language: str) -> int:
    """The version of the wording a visitor sees in `language`: an unedited v1 default is shown
    as v2 (it gets the added sentence), so it is reported as at least 2. Owner-edited text keeps
    its own version."""
    version = config.leads.consent.notice_version
    texts = _plain(config.leads.consent.notice)
    lang = language if texts.get(language) else "en"
    return max(version, NOTICE_VERSION) if _is_v1_default(config, lang) else version


# /TV2 reference
def notice_for(config: "AssistantConfig", language: str) -> str:
    """The exact Markdown text shown: placeholders filled, English when the language has none.

    Owner-editable text may contain braces, so the tokens are replaced one by one
    (never `str.format`): anything else in braces stays as typed.
    """
    consent = config.leads.consent
    texts: dict[str, str] = {k: v for k, v in consent.notice.items()} or DEFAULT_NOTICES
    lang = language if texts.get(language) else "en"
    template = texts.get(lang) or DEFAULT_NOTICES[_lang(lang, DEFAULT_NOTICES)]
    if _is_v1_default(config, lang):  # TV2 reference: never overwrite the owner's own words
        template = _plain(_NOTICE)[lang]
    card = config.identity.card
    fallback = _NO_CONTACT[_lang(lang, _NO_CONTACT)]
    # P3 legal: the business's own policy wins (it is the data fiduciary); else the deployer's
    # published privacy notice, when there is one
    # (the deployer's address rides on the config, set by `get_runtime_config`, so every surface
    # records the same wording)
    policy_url = consent.privacy_policy_url or config.deployer_privacy_url
    # /P3 legal
    link = f" [{_POLICY[_lang(lang, _POLICY)]}]({policy_url})" if policy_url else ""
    filled = (
        template.replace("{business_name}", config.identity.business_name or fallback)
        .replace("{purpose}", consent.purpose)
        .replace("{retention_days}", str(consent.retention_days))
        .replace("{contact}", card.email or card.phone or fallback)
        .replace("{privacy_link}", link)
    )
    return fill_persona(filled, config)


def consent_label(language: str) -> str:
    return _LABELS[_lang(language, _LABELS)]


def wording_hash(text: str) -> str:
    return hashlib.sha256(unicodedata.normalize("NFC", text).encode("utf-8")).hexdigest()


def consent_row(
    *,
    lead_id: uuid.UUID,
    corpus_id: uuid.UUID,
    config: "AssistantConfig",
    language: str,
    notice_text: str,
    ip: str | None,
) -> LeadConsentRow:
    return LeadConsentRow(
        lead_id=lead_id,
        corpus_id=corpus_id,
        notice_version=notice_version_for(config, language),  # TV2 reference
        wording_hash=wording_hash(notice_text),
        purpose=config.leads.consent.purpose[:300],
        language=language[:16],
        ip=ip[:64] if ip else None,
    )
