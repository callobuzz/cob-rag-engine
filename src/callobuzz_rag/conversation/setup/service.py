"""The guided-setup state machine over `assistant_setup_sessions` (C8).

One method per step; the routes (`api/routes/assistant_setup.py`) translate the errors raised
here. Nothing here answers a visitor, so no step takes the monthly question cap; the model
calls it does make (proposal, wording, document questions) are metered as usage source `setup`
with `requests=0` (tokens and cost, no question counted).
"""

import logging
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.billing.context import is_admin_request, mark_admin_answer
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.setup.autotests import (
    build_setup_cases,
    document_cases,
    gaps_from_samples,
    section_headings,
    template_cases,
)
from callobuzz_rag.conversation.setup.generate import GenerationResult, generate_config
from callobuzz_rag.conversation.setup.interview import validate_answers, visible_questions
from callobuzz_rag.conversation.setup.propose import (
    PageText,
    SetupLLMUnavailableError,
    propose_from_pages,
)
from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.db.models.assistant_setup import AssistantSetupSession
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.golden_pair import GoldenPair
from callobuzz_rag.db.models.golden_set import GoldenSet

logger = logging.getLogger(__name__)

_OPEN = ("started", "proposed", "interviewing", "generated", "tested")
# The checks are plan-only (label, route and gate; no answer, no judge): they finish in
# seconds and spend no questions. A later "run with answers" is this one value.
TEST_MODE = "fast"
SETUP_RUN_NAME = "Guided setup checks"


class SetupNotFoundError(RAGKernelError):
    """No such setup session for this knowledge base."""


class SetupStepError(RAGKernelError):
    def __init__(self, status: str, expected: list[str]) -> None:
        super().__init__(f"setup step invalid in status {status}")
        self.status = status
        self.expected = expected


def _require(row: AssistantSetupSession, allowed: tuple[str, ...]) -> None:
    if row.status not in allowed:
        raise SetupStepError(row.status, list(allowed))


def profile_from_proposal(
    proposal: dict[str, Any] | None, website_url: str | None
) -> dict[str, Any]:
    """What the confirm screen starts from: the crawl's findings, or empty fields."""
    found = proposal or {}
    return {
        "business_name": found.get("business_name"),
        "phone": found.get("phone"),
        "whatsapp": found.get("whatsapp"),
        "email": found.get("email"),
        "address": found.get("address"),
        "website": website_url,
        "hours": found.get("hours") or [],
    }


class SetupService:
    def __init__(self, session: AsyncSession, request: Request) -> None:
        self._s = session
        self._request = request

    # ---------- helpers

    @property
    def _settings(self) -> Any:
        return self._request.app.state.settings

    async def _llm(self, corpus: Corpus) -> tuple[Any, Any]:
        """One provider per step, resolved like the answering routes (X-LLM-* headers, then
        the knowledge base's own llm_config, then the engine default, with the business's
        pipeline profile laid over it: PIP1), so its cost ledger is the whole step."""
        from callobuzz_rag.api.routes.query import _extract_llm_headers, _get_config_resolver
        from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
        from callobuzz_rag.tiers import profiles as tier_profiles
        from callobuzz_rag.tiers.resolve import resolve_with_profile

        # PIP1 profile
        resolved, _retrieval = resolve_with_profile(
            _get_config_resolver(self._request),
            corpus,
            headers=_extract_llm_headers(self._request) or None,
            profile=await tier_profiles.plan_profile(
                self._s, corpus.tenant_id, settings=self._settings
            ),
            settings=self._settings,
        )
        # /PIP1 profile
        return LiteLLMProvider.from_resolved_config(resolved), resolved

    async def _meter(self, corpus: Corpus, llm: Any, resolved: Any) -> None:
        """Add the step's tokens and cost to usage (`requests=0`: no question is counted)."""
        from callobuzz_rag.analytics.usage import SOURCE_SETUP, payer_of, record_usage
        from callobuzz_rag.llm.cost import request_cost_of

        cost = request_cost_of(llm)
        if cost is None:
            return
        mark_admin_answer(self._request)  # BIL3: the operator's setup work is not billed
        await record_usage(
            self._s,
            corpus_id=corpus.id,
            tenant_id=corpus.tenant_id,
            source=SOURCE_SETUP,
            prompt_tokens=0,
            completion_tokens=0,
            model=getattr(resolved, "model", None),
            requests=0,
            cost=cost,
            payer=payer_of(resolved),
            commit=False,
        )

    def _store(self) -> Any:
        from callobuzz_rag.conversation.store import AssistantConfigStore

        return AssistantConfigStore(self._s, self._request, self._settings)

    def _encryption_available(self) -> bool:
        from callobuzz_rag.core.secrets import parse_key_list

        return bool(parse_key_list(self._settings.encryption_key))

    async def _commit(self, row: AssistantSetupSession) -> AssistantSetupSession:
        await self._s.commit()
        await self._s.refresh(row)
        return row

    # ---------- steps

    async def start(
        self, corpus: Corpus, business_type: str | None, website_url: str | None
    ) -> AssistantSetupSession:
        older = (
            (
                await self._s.execute(
                    select(AssistantSetupSession).where(
                        AssistantSetupSession.corpus_id == corpus.id,
                        AssistantSetupSession.status.in_(_OPEN),
                    )
                )
            )
            .scalars()
            .all()
        )
        for old in older:
            old.status = "abandoned"
            # The checks set of an abandoned session would otherwise keep running on every
            # publish (C7 queues one fast run per set); the runs stay, their set_id goes NULL.
            if old.golden_set_id is not None:
                stale = await self._s.get(GoldenSet, old.golden_set_id)
                if stale is not None and stale.source == "setup":
                    await self._s.delete(stale)
                old.golden_set_id = None
        row = AssistantSetupSession(
            corpus_id=corpus.id,
            status="started",
            answers={},
            provenance={},
            business_type=business_type,
            website_url=website_url,
            created_by=actor_from_request(self._request)[2][:320],
        )
        self._s.add(row)
        return await self._commit(row)

    async def get(self, corpus: Corpus, sid: uuid.UUID) -> AssistantSetupSession:
        row = (
            await self._s.execute(
                select(AssistantSetupSession).where(
                    AssistantSetupSession.id == sid, AssistantSetupSession.corpus_id == corpus.id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise SetupNotFoundError()
        return row

    async def propose(
        self, corpus: Corpus, sid: uuid.UUID, pages: list[PageText], crawl_job_id: str | None
    ) -> AssistantSetupSession:
        row = await self.get(corpus, sid)
        _require(row, ("started", "proposed"))
        llm, resolved = await self._llm(corpus)
        proposal = await propose_from_pages(llm, pages)
        await self._meter(corpus, llm, resolved)
        row.proposal = proposal
        row.crawl_job_id = crawl_job_id
        row.business_type = row.business_type or proposal["business_type"]
        row.status = "proposed"
        return await self._commit(row)

    async def questions(
        self, corpus: Corpus, sid: uuid.UUID
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """The visible questions (with the owner's earlier answers or the crawl's prefill) and
        the confirm-screen profile (the owner's, else the crawl's)."""
        row = await self.get(corpus, sid)
        views = visible_questions(row.business_type or "generic_support", row.proposal)
        items = [
            {
                "id": v["question"].id,
                "kind": v["question"].kind,
                "title": v["question"].title,
                "help": v["question"].help,
                "options": [{"value": o.value, "label": o.label} for o in v["question"].options],
                "other": v["question"].other,
                "required": v["question"].required,
                "max_items": v["question"].max_items,
                "prefill": row.answers.get(v["question"].id, v["prefill"]),
                "skipped_reason": v["skipped_reason"],
            }
            for v in views
        ]
        profile = row.profile or profile_from_proposal(row.proposal, row.website_url)
        return items, profile

    async def answer(
        self,
        corpus: Corpus,
        sid: uuid.UUID,
        business_type: str | None,
        answers: dict[str, Any],
        profile: dict[str, Any] | None,
        *,
        partial: bool = False,
    ) -> AssistantSetupSession:
        row = await self.get(corpus, sid)
        _require(row, _OPEN)
        btype = business_type or row.business_type or "generic_support"
        row.answers = validate_answers(answers, btype, partial=partial)
        row.business_type = btype
        if profile is not None:
            row.profile = profile
        row.status = "interviewing"
        return await self._commit(row)

    async def _current_config(
        self, store: Any, corpus: Corpus, row: AssistantSetupSession
    ) -> tuple[AssistantConfig | None, int]:
        """The owner's existing assistant (its draft, else the published one) and the draft's
        revision. A draft this session wrote earlier is not "the owner's": it is replaced."""
        from callobuzz_rag.conversation.store import row_config

        published, draft = await store.state(corpus.id)
        revision = draft.draft_revision if draft is not None else 0
        source = draft or published
        if (
            draft is not None
            and row.generated_config is not None
            and draft.config == row.generated_config
        ):
            source = published
        return (row_config(source) if source is not None else None), revision

    async def generate(
        self, corpus: Corpus, sid: uuid.UUID, *, wording: str, base_revision: int | None
    ) -> tuple[AssistantSetupSession, int, GenerationResult]:
        from callobuzz_rag.conversation.store import prompt_budget, publish_problems

        row = await self.get(corpus, sid)
        _require(row, ("interviewing", "generated", "tested"))
        # Progress may have been saved with questions still open: every required one is
        # needed now (InterviewAnswerError names the first, the route puts it on that question).
        validate_answers(row.answers, row.business_type or "generic_support")
        llm: Any = None
        resolved: Any = None
        if wording == "ai":
            llm, resolved = await self._llm(corpus)
            if not llm.has_credentials:
                raise SetupLLMUnavailableError("No LLM credential for this knowledge base")
        store = self._store()
        current, revision = await self._current_config(store, corpus, row)
        encryption = self._encryption_available()
        result = await generate_config(
            business_type=row.business_type or "generic_support",
            proposal=row.proposal,
            answers=row.answers,
            llm=llm,
            profile=row.profile,
            current=current,
            encryption_available=encryption,
            website_url=row.website_url,
        )
        if llm is not None:
            await self._meter(corpus, llm, resolved)
            # The wording call is paid for whatever the checks below decide: record it now.
            await self._s.commit()
        # Fail here, not at Publish: rule limit, rule and prompt budgets, bad references.
        publish_problems(
            result.config,
            encryption_available=encryption,
            prompt_budget=prompt_budget(self._settings),
        )
        draft = await store.save_draft(
            corpus, result.config, revision if base_revision is None else base_revision
        )
        row.generated_config = result.config.model_dump(mode="json")
        row.provenance = result.provenance
        row.status = "generated"
        await self._commit(row)
        return row, draft.draft_revision, result

    async def run_tests(self, corpus: Corpus, sid: uuid.UUID) -> tuple[AssistantSetupSession, int]:
        """Create a C7 golden set (`source="setup"`) of behaviour-only pairs and start a golden
        run against the draft (`triggered_by="setup"`, mode `TEST_MODE`)."""
        from callobuzz_rag.conversation.templates import load_template
        from callobuzz_rag.evaluation.golden_runner import create_golden_set, start_golden_run

        row = await self.get(corpus, sid)
        _require(row, ("generated", "tested"))
        template = load_template(row.business_type or "generic_support")
        headings = await section_headings(self._s, corpus.id)
        doc: list[Any] = []
        if headings:
            try:
                llm, resolved = await self._llm(corpus)
                doc = await document_cases(llm, headings)
                await self._meter(corpus, llm, resolved)
            except Exception:
                logger.warning("Setup document questions skipped", exc_info=True)
        profile = str(
            (row.generated_config or {}).get("scope", {}).get("sensitive_profile", "none")
        )
        pairs = build_setup_cases(
            template_tests=template_cases(template.tests), doc_cases=doc, profile=profile
        )
        golden_set = await self._setup_set(corpus, row, pairs, create_golden_set)
        run = await start_golden_run(
            self._s,
            corpus.id,
            golden_set.id,
            mode=TEST_MODE,
            triggered_by="setup",
            assistant="draft",
            created_by=actor_from_request(self._request)[2][:320],
            admin_initiated=is_admin_request(self._request),
        )
        row.golden_set_id, row.eval_run_id, row.status = golden_set.id, run.id, "tested"
        await self._commit(row)
        return row, len(pairs)

    async def _setup_set(
        self, corpus: Corpus, row: AssistantSetupSession, pairs: list[Any], create: Any
    ) -> GoldenSet:
        """This session's one checks set: the first run creates it, later runs replace its
        pairs (a set per run would queue a duplicate fast run per set on every publish)."""
        from callobuzz_rag.evaluation.golden import pair_from_fields

        existing = await self._s.get(GoldenSet, row.golden_set_id) if row.golden_set_id else None
        if existing is None or existing.corpus_id != corpus.id or existing.source != "setup":
            created: GoldenSet = await create(
                self._s, corpus.id, name=SETUP_RUN_NAME, source="setup", pairs=pairs
            )
            return created
        await self._s.execute(delete(GoldenPair).where(GoldenPair.set_id == existing.id))
        for position, pair in enumerate(pairs):
            self._s.add(pair_from_fields(corpus.id, existing.id, pair, position))
        await self._s.commit()
        return existing

    async def gaps(self, corpus: Corpus, sid: uuid.UUID) -> tuple[str, list[dict[str, str]]]:
        from callobuzz_rag.db.models.eval_run import EvalRun
        from callobuzz_rag.db.models.eval_run_sample import EvalRunSample

        row = await self.get(corpus, sid)
        if row.eval_run_id is None:
            raise SetupStepError(row.status, ["tested"])
        run = await self._s.get(EvalRun, row.eval_run_id)
        if run is None or run.status != "completed":
            return (run.status if run else "missing"), []
        samples = (
            (
                await self._s.execute(
                    select(EvalRunSample)
                    .where(EvalRunSample.run_id == run.id)
                    .order_by(EvalRunSample.position)
                )
            )
            .scalars()
            .all()
        )
        return run.status, gaps_from_samples(list(samples))

    async def apply(
        self, corpus: Corpus, sid: uuid.UUID, *, base_revision: int, note: str | None
    ) -> tuple[AssistantSetupSession, int]:
        """Publish through C2's store (its checks and `assistant.published` audit run
        unchanged), then do what C2's publish route does after its commit: queue the
        business-card sync and C7's publish-time fast runs."""
        from callobuzz_rag.conversation.business_card import dispatch_business_card

        row = await self.get(corpus, sid)
        _require(row, ("generated", "tested"))
        store = self._store()
        published = await store.publish(
            corpus, note=note or "Guided setup", base_revision=base_revision
        )
        row.status = "applied"
        await record_audit(
            self._s,
            self._request,
            "assistant.setup_applied",
            tenant_id=corpus.tenant_id,
            target_type="corpus",
            target_id=str(corpus.id),
            details={
                "corpus_id": str(corpus.id),
                "session_id": str(row.id),
                "version": published.version,
            },
        )
        await self._commit(row)
        if store.pending_card is not None:
            try:
                await dispatch_business_card(
                    self._s,
                    corpus,
                    store.pending_card,
                    getattr(self._request.app.state, "vector_store", None),
                )
            except Exception:
                logger.warning(
                    "Could not queue the business card for corpus %s", corpus.id, exc_info=True
                )
        version = int(published.version or 0)
        if published.config.get("enabled", True):
            try:
                from callobuzz_rag.evaluation.golden_runner import enqueue_publish_runs

                await enqueue_publish_runs(
                    self._s, corpus.id, version, admin_initiated=is_admin_request(self._request)
                )
            except Exception:
                logger.warning("Publish test runs not queued for %s", corpus.id, exc_info=True)
        return row, version
