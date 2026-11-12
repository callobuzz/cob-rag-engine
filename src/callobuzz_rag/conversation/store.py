"""AssistantConfigStore: one draft, one published, archived history per knowledge base (C2).

Every method flushes and never commits: the route commits once, so the change and its audit row
(`record_audit`) land together. Every writer takes the corpus row lock (FOR NO KEY UPDATE, which
does not block foreign-key inserts) FIRST, before it reads any assistant row, so the revision
check and the version numbering see what the previous writer committed; the partial unique
indexes are the backstop. The store only reads `corpus.id`, `tenant_id`,
`prompt_config` and `qdrant_collection`, and never writes to the corpus (a `ck_` key request
carries a detached copy of it).
"""

import copy
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import func, select

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.conversation.business_card import PendingCard, sync_business_card
from callobuzz_rag.conversation.compiler import (
    MAX_ENABLED_RULES,
    RULES_TOKEN_BUDGET,
    rules_section_tokens,
    worst_case_prompt_tokens,
)
from callobuzz_rag.conversation.schema import CURRENT_SCHEMA_VERSION, AssistantConfig, load_config
from callobuzz_rag.conversation.templates import TEMPLATE_KEYS, load_template
from callobuzz_rag.conversation.types import LANG_KEYS
from callobuzz_rag.core.exceptions import AssistantSchemaTooNewError
from callobuzz_rag.core.secrets import parse_key_list
from callobuzz_rag.db.models.assistant_config import (
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    AssistantConfigVersion,
)
from callobuzz_rag.db.models.corpus import Corpus

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings

# What the system prompt may use of the context window, leaving the rest for the question,
# the prompt's own scaffolding and at least some retrieved context.
_PROMPT_HEADROOM_TOKENS = 1500


@dataclass(frozen=True)
class RuntimeConfig:
    config: AssistantConfig
    version: int | None  # None = the draft (preview)
    draft_revision: int | None  # set only when the draft was chosen (C1 label-cache scope)
    enabled: bool


def row_config(row: AssistantConfigVersion) -> AssistantConfig:
    try:
        return load_config(row.config)
    except AssistantSchemaTooNewError as exc:
        raise APIError(
            409,
            "This assistant was saved by a newer engine version.",
            "assistant_schema_unsupported",
        ) from exc


async def get_runtime_config(
    session: "AsyncSession", corpus_id: uuid.UUID, *, use_draft: bool = False
) -> RuntimeConfig | None:
    """The published config, or the draft when `use_draft` and one exists.

    None only when the knowledge base has no published row (after `phase12_001` every one has).
    The whole JSONB is loaded on every call (a template-sized config is 9-11 KB); C1 memoises
    it per (corpus, version, draft_revision) for a few seconds.
    """
    statuses = [STATUS_PUBLISHED, STATUS_DRAFT] if use_draft else [STATUS_PUBLISHED]
    rows = (
        (
            await session.execute(
                select(AssistantConfigVersion).where(
                    AssistantConfigVersion.corpus_id == corpus_id,
                    AssistantConfigVersion.status.in_(statuses),
                )
            )
        )
        .scalars()
        .all()
    )
    published = next((r for r in rows if r.status == STATUS_PUBLISHED), None)
    draft = next((r for r in rows if r.status == STATUS_DRAFT), None)
    chosen = draft if (use_draft and draft is not None) else published
    if chosen is None or published is None:
        return None
    config = row_config(chosen)
    # P3 legal: the consent notice's fallback link, resolved here so no surface depends on
    # which process last served a widget request
    from callobuzz_rag.legal import privacy_link

    await privacy_link.ensure(session)  # never raises
    config.with_deployer_privacy_url(privacy_link.consent_url())
    # /P3 legal
    return RuntimeConfig(
        config=config,
        version=chosen.version,
        draft_revision=chosen.draft_revision if chosen.status == STATUS_DRAFT else None,
        enabled=config.enabled,
    )


def default_config_for(corpus: Corpus, *, encryption_available: bool = True) -> AssistantConfig:
    """The "Customer support" template plus whatever the corpus's old `prompt_config` carried.

    `system_prompt` and `constraints` become `advanced.extra_instructions` unshortened;
    `response_language` becomes the fixed reply language when it is one of the assistant's
    language keys, else a line "Always reply in <value>." is appended. Mirrors the data step
    of migration phase12_001.
    """
    config = load_template("generic_support").config.model_copy(deep=True)
    if not encryption_available:
        config.leads.enabled = False
    prompt = corpus.prompt_config if isinstance(corpus.prompt_config, dict) else {}
    extra = [str(prompt[k]).strip() for k in ("system_prompt", "constraints") if prompt.get(k)]
    extra = [e for e in extra if e]
    language = str(prompt.get("response_language") or "").strip()
    if language in LANG_KEYS:
        config.language.primary = language  # type: ignore[assignment]
        config.language.reply_policy = "fixed"
        if language not in config.language.supported:
            config.language.supported.append(language)  # type: ignore[arg-type]
    elif language:
        extra.append(f"Always reply in {language}.")
    if extra:
        config.advanced.extra_instructions = "\n\n".join(extra)
    return config


def merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """RFC 7396 JSON merge patch; returns a new dict."""
    result = copy.deepcopy(target)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_patch(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _invalid(path: str, what: str) -> APIError:
    return APIError(
        422,
        f"{path} names {what}, which does not exist",
        "assistant_reference_invalid",
        path=path,
    )


def prompt_budget(settings: "Settings") -> int:
    """Tokens the compiled system prompt may use (see `_PROMPT_HEADROOM_TOKENS`)."""
    return settings.context_max_tokens - settings.response_reserve_tokens - _PROMPT_HEADROOM_TOKENS


def publish_problems(
    config: AssistantConfig, *, encryption_available: bool, prompt_budget: int | None = None
) -> None:
    """Checks a config must pass to go live (drafts may be incomplete, so not in the schema)."""
    enabled = sum(1 for r in config.rules if r.enabled)
    if enabled > MAX_ENABLED_RULES:
        raise APIError(
            422,
            f"At most {MAX_ENABLED_RULES} guidance rules can be switched on",
            "assistant_rule_limit",
            limit=MAX_ENABLED_RULES,
            enabled=enabled,
        )
    used = rules_section_tokens(config)
    if used > RULES_TOKEN_BUDGET:
        raise APIError(
            422,
            "The guidance rules are too long; shorten or switch some off",
            "assistant_rule_budget",
            budget=RULES_TOKEN_BUDGET,
            used=used,
        )
    topics = {t.id for t in config.scope.topics}
    for i, rule in enumerate(config.rules):
        if rule.when and rule.when.topic and rule.when.topic not in topics:
            raise _invalid(f"rules[{i}].when.topic", f"topic '{rule.when.topic}'")
    for tid in config.leads.triggers.topics:
        if tid not in topics:
            raise _invalid("leads.triggers.topics", f"topic '{tid}'")
    for i, custom in enumerate(config.leads.custom_fields):
        if custom.id == "intent":
            raise APIError(
                422,
                f"leads.custom_fields[{i}].id 'intent' is reserved; pick another id",
                "assistant_reference_invalid",
                path=f"leads.custom_fields[{i}].id",
            )
    fields = {"name", "phone", "email", "intent"} | {f.id for f in config.leads.custom_fields}
    for i, q in enumerate(config.leads.qualification):
        for j, cond in enumerate(q.conditions):
            if cond.field not in fields:
                raise _invalid(
                    f"leads.qualification[{i}].conditions[{j}].field", f"field '{cond.field}'"
                )
    if prompt_budget is not None:
        total = worst_case_prompt_tokens(config)
        if total > prompt_budget:
            raise APIError(
                422,
                "The assistant's instructions are too long for the model's context window; "
                "shorten the expert instructions, the topics or the guidance rules",
                "assistant_prompt_budget",
                budget=prompt_budget,
                used=total,
            )
    if config.leads.enabled and not encryption_available:
        raise APIError(
            503,
            "Leads need RAG_ENCRYPTION_KEY: set it, or switch leads off",
            "encryption_not_configured",
        )


def next_notice_version(previous: AssistantConfig | None, config: AssistantConfig) -> int:
    """The consent notice version: bumped when the wording changed, never taken from a client."""
    if previous is None:
        # TV2 reference: a new knowledge base starts on the v2 default wording
        from callobuzz_rag.conversation.schema import _NOTICE, NOTICE_VERSION

        return NOTICE_VERSION if config.leads.consent.notice == _NOTICE else 1
    old = previous.leads.consent
    if old.notice != config.leads.consent.notice:
        return old.notice_version + 1
    return old.notice_version


class AssistantConfigStore:
    def __init__(
        self, session: "AsyncSession", request: "Request | None", settings: "Settings"
    ) -> None:
        self._s = session
        self._request = request
        self._settings = settings
        # Set by publish / restore / set_enabled / publish_initial: the route dispatches it
        # AFTER the commit (the job row must exist before the worker looks for it).
        self.pending_card: PendingCard | None = None

    # ---------- reads
    async def _rows(self, corpus_id: uuid.UUID) -> list[AssistantConfigVersion]:
        result = await self._s.execute(
            select(AssistantConfigVersion).where(AssistantConfigVersion.corpus_id == corpus_id)
        )
        return list(result.scalars().all())

    async def state(
        self, corpus_id: uuid.UUID
    ) -> tuple[AssistantConfigVersion | None, AssistantConfigVersion | None]:
        """(published, draft)."""
        rows = await self._rows(corpus_id)
        return (
            next((r for r in rows if r.status == STATUS_PUBLISHED), None),
            next((r for r in rows if r.status == STATUS_DRAFT), None),
        )

    async def list_versions(
        self, corpus_id: uuid.UUID, offset: int, limit: int
    ) -> tuple[list[AssistantConfigVersion], int]:
        base = (AssistantConfigVersion.corpus_id == corpus_id) & (
            AssistantConfigVersion.version.is_not(None)
        )
        total = (
            await self._s.execute(select(func.count(AssistantConfigVersion.id)).where(base))
        ).scalar()
        rows = (
            await self._s.execute(
                select(AssistantConfigVersion)
                .where(base)
                .order_by(AssistantConfigVersion.version.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
        return list(rows.all()), int(total or 0)

    async def get_version(self, corpus_id: uuid.UUID, version: int) -> AssistantConfigVersion:
        row = (
            await self._s.execute(
                select(AssistantConfigVersion).where(
                    AssistantConfigVersion.corpus_id == corpus_id,
                    AssistantConfigVersion.version == version,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise APIError(
                404, f"Assistant version {version} not found", "assistant_version_not_found"
            )
        return row

    # ---------- drafts
    def _actor(self) -> tuple[str, uuid.UUID | None]:
        _kind, user_id, label = actor_from_request(self._request)
        return label[:320], user_id

    @staticmethod
    def _check_revision(draft: AssistantConfigVersion | None, base_revision: int | None) -> None:
        current = draft.draft_revision if draft is not None else 0
        if (base_revision or 0) != current:
            raise APIError(
                409,
                "Someone else changed this draft; reload it and try again",
                "assistant_draft_conflict",
                current_revision=current,
            )

    async def save_draft(
        self, corpus: Corpus, config: AssistantConfig, base_revision: int | None
    ) -> AssistantConfigVersion:
        await self._lock(corpus.id)
        _published, draft = await self.state(corpus.id)
        self._check_revision(draft, base_revision)
        data = config.model_dump(mode="json")
        label, user_id = self._actor()
        if draft is None:
            draft = AssistantConfigVersion(
                corpus_id=corpus.id,
                version=None,
                status=STATUS_DRAFT,
                config=data,
                schema_version=CURRENT_SCHEMA_VERSION,
                draft_revision=1,
                created_by=label,
                created_by_user_id=user_id,
            )
            self._s.add(draft)
        else:
            draft.config = data
            draft.draft_revision += 1
            draft.created_by = label
            draft.created_by_user_id = user_id
        await self._s.flush()
        return draft

    async def _base_config(self, corpus: Corpus) -> AssistantConfig:
        published, draft = await self.state(corpus.id)
        if draft is not None:
            return row_config(draft)
        if published is not None:
            return row_config(published)
        return default_config_for(corpus, encryption_available=self._encryption_available())

    async def patch_draft(
        self, corpus: Corpus, patch: dict[str, Any], base_revision: int | None
    ) -> AssistantConfigVersion:
        await self._lock(corpus.id)
        base = (await self._base_config(corpus)).model_dump(mode="json")
        merged = merge_patch(base, patch)
        try:
            config = AssistantConfig.model_validate(merged)
        except ValidationError as exc:
            # Same 422 shape as PUT: loc = body > config > <section> > <field>.
            errors = json.loads(exc.json(include_url=False))
            raise RequestValidationError(
                [{**err, "loc": ("body", "config", *err["loc"])} for err in errors]
            ) from exc
        return await self.save_draft(corpus, config, base_revision)

    async def draft_from_template(
        self, corpus: Corpus, key: str, keep_identity: bool, base_revision: int | None
    ) -> AssistantConfigVersion:
        if key not in TEMPLATE_KEYS:
            raise APIError(404, f"No template named '{key}'", "template_not_found")
        await self._lock(corpus.id)
        template = load_template(key)
        config = template.config.model_copy(deep=True)
        if keep_identity:
            current = await self._base_config(corpus)
            config.identity = current.identity.model_copy(deep=True)
            config.identity.business_type = template.config.identity.business_type
            config.advanced = current.advanced.model_copy(deep=True)
        return await self.save_draft(corpus, config, base_revision)

    async def discard_draft(self, corpus: Corpus) -> None:
        await self._lock(corpus.id)
        _published, draft = await self.state(corpus.id)
        if draft is None:
            raise APIError(404, "There is no draft to discard", "assistant_draft_missing")
        await self._s.delete(draft)
        await record_audit(
            self._s,
            self._request,
            "assistant.draft_discarded",
            tenant_id=corpus.tenant_id,
            target_type="corpus",
            target_id=str(corpus.id),
            details={"corpus_id": str(corpus.id)},
        )
        await self._s.flush()

    # ---------- publishing
    async def _lock(self, corpus_id: uuid.UUID) -> None:
        """Serialise assistant writers on one knowledge base. `key_share` renders FOR NO KEY
        UPDATE: it conflicts with other writers' lock but not with the FOR KEY SHARE every
        insert into a corpus-FK table (documents, usage, jobs) takes, so ingestion goes on."""
        await self._s.execute(
            select(Corpus.id).where(Corpus.id == corpus_id).with_for_update(key_share=True)
        )

    def _encryption_available(self) -> bool:
        return bool(parse_key_list(self._settings.encryption_key))

    def _check_publishable(self, config: AssistantConfig) -> None:
        publish_problems(
            config,
            encryption_available=self._encryption_available(),
            prompt_budget=prompt_budget(self._settings),
        )

    async def _new_published(
        self,
        corpus: Corpus,
        config: AssistantConfig,
        *,
        note: str | None,
        restored_from: int | None,
        previous: AssistantConfigVersion | None,
    ) -> AssistantConfigVersion:
        rows = await self._rows(corpus.id)
        number = max((r.version or 0 for r in rows), default=0) + 1
        prev_config = row_config(previous) if previous is not None else None
        config.leads.consent.notice_version = next_notice_version(prev_config, config)
        if previous is not None:
            previous.status = STATUS_ARCHIVED
            await self._s.flush()  # free the partial unique index before the insert
        label, user_id = self._actor()
        row = AssistantConfigVersion(
            corpus_id=corpus.id,
            version=number,
            status=STATUS_PUBLISHED,
            config=config.model_dump(mode="json"),
            schema_version=CURRENT_SCHEMA_VERSION,
            draft_revision=0,
            created_by=label,
            created_by_user_id=user_id,
            published_at=datetime.now(UTC),
            published_by_label=label,
            note=note,
            restored_from=restored_from,
        )
        self._s.add(row)
        self.pending_card = await sync_business_card(self._s, corpus, config, prev_config)
        await self._s.flush()
        return row

    async def publish(
        self, corpus: Corpus, *, note: str | None, base_revision: int
    ) -> AssistantConfigVersion:
        await self._lock(corpus.id)
        published, draft = await self.state(corpus.id)
        # A stale revision first: a draft that a concurrent publish already took is a conflict
        # (the caller reviewed revision N), not a missing draft.
        self._check_revision(draft, base_revision)
        if draft is None:
            raise APIError(404, "There is no draft to publish", "assistant_draft_missing")
        config = row_config(draft)
        if published is not None:
            # The on/off switch belongs to the live version (set_enabled, restore); a draft
            # saved while the assistant was on must not switch a disabled one back on.
            config.enabled = row_config(published).enabled
        self._check_publishable(config)
        await self._s.delete(draft)
        row = await self._new_published(
            corpus, config, note=note, restored_from=None, previous=published
        )
        await record_audit(
            self._s,
            self._request,
            "assistant.published",
            tenant_id=corpus.tenant_id,
            target_type="corpus",
            target_id=str(corpus.id),
            details={
                "corpus_id": str(corpus.id),
                "version": row.version,
                "from_version": published.version if published else None,
                "note": note,
                "enabled": config.enabled,
            },
        )
        return row

    async def publish_initial(
        self, corpus: Corpus, config: AssistantConfig, note: str
    ) -> AssistantConfigVersion:
        await self._lock(corpus.id)
        row = await self._new_published(
            corpus, config, note=note, restored_from=None, previous=None
        )
        await record_audit(
            self._s,
            self._request,
            "assistant.published",
            tenant_id=corpus.tenant_id,
            target_type="corpus",
            target_id=str(corpus.id),
            details={
                "corpus_id": str(corpus.id),
                "version": row.version,
                "from_version": None,
                "note": note,
                "enabled": config.enabled,
            },
        )
        return row

    async def restore(self, corpus: Corpus, version: int) -> AssistantConfigVersion:
        await self._lock(corpus.id)
        old = await self.get_version(corpus.id, version)
        published, _draft = await self.state(corpus.id)
        config = row_config(old)
        if published is not None:
            # A restore changes content, never the on/off switch (set_enabled does that).
            config.enabled = row_config(published).enabled
        self._check_publishable(config)
        row = await self._new_published(
            corpus,
            config,
            note=f"Restored version {version}",
            restored_from=version,
            previous=published,
        )
        await record_audit(
            self._s,
            self._request,
            "assistant.rolled_back",
            tenant_id=corpus.tenant_id,
            target_type="corpus",
            target_id=str(corpus.id),
            details={
                "corpus_id": str(corpus.id),
                "version": row.version,
                "restored_from": version,
                "enabled": config.enabled,
            },
        )
        return row

    async def set_enabled(self, corpus: Corpus, enabled: bool) -> AssistantConfigVersion:
        await self._lock(corpus.id)
        published, _draft = await self.state(corpus.id)
        if published is None:
            raise APIError(
                404, "This knowledge base has no published assistant", "assistant_not_found"
            )
        config = row_config(published)
        if config.enabled == enabled:
            return published
        config.enabled = enabled
        row = await self._new_published(
            corpus,
            config,
            note="Switched on" if enabled else "Switched off",
            restored_from=None,
            previous=published,
        )
        await record_audit(
            self._s,
            self._request,
            "assistant.enabled_changed",
            tenant_id=corpus.tenant_id,
            target_type="corpus",
            target_id=str(corpus.id),
            details={"corpus_id": str(corpus.id), "version": row.version, "enabled": enabled},
        )
        return row
