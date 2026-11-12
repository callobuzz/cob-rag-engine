"""Guided assistant setup (conversation C8): /api/v1/corpora/{corpus_id}/assistant/setup.

Any key that reaches the knowledge base may run every step (members too, like drafts); only
`apply`, which publishes, is owner-only (A5 `require_owner`). For an API key, reads need the
`read` permission and every other step `write` (`api/permissions.py`). The dashboard crawls the
website through the scraper service and hands page text to `propose`; the engine never calls
the scraper.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, path_corpus, require_owner
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.assistant_setup import (
    SetupAnswersRequest,
    SetupApplyRequest,
    SetupApplyResponse,
    SetupGapsResponse,
    SetupGenerateRequest,
    SetupGenerateResponse,
    SetupProposalResponse,
    SetupProposeRequest,
    SetupQuestionsResponse,
    SetupSessionResponse,
    SetupStartRequest,
    SetupTestsResponse,
)
from callobuzz_rag.conversation.setup.embedding import multilingual_flag
from callobuzz_rag.conversation.setup.interview import InterviewAnswerError
from callobuzz_rag.conversation.setup.propose import PageText, SetupLLMUnavailableError
from callobuzz_rag.conversation.setup.service import (
    SetupNotFoundError,
    SetupService,
    SetupStepError,
)
from callobuzz_rag.db.models.corpus import Corpus

router = APIRouter(prefix="/api/v1/corpora/{corpus_id}/assistant/setup", tags=["assistant-setup"])

_HANDLED = (SetupNotFoundError, SetupStepError, SetupLLMUnavailableError)


def _answer_error(exc: InterviewAnswerError) -> RequestValidationError:
    """A 422 whose `loc` is the question, so a form puts the message on that question."""
    return RequestValidationError(
        [{"loc": ("body", "answers", exc.question_id), "type": "value_error", "msg": exc.reason}]
    )


def _out(row: object, corpus: Corpus, request: Request) -> SetupSessionResponse:
    out = SetupSessionResponse.model_validate(row)
    out.embedding_multilingual = multilingual_flag(
        corpus.applied_embedding, request.app.state.settings
    )
    languages = {v for v in (out.answers.get("languages") or []) if isinstance(v, str)}
    out.recommend_multilingual = bool(
        {"hi", "hi-Latn"} & languages and out.embedding_multilingual is False
    )
    return out


def _translate(exc: Exception) -> APIError:
    if isinstance(exc, SetupNotFoundError):
        return APIError(
            404, "That setup session was not found. Start a new one.", "setup_session_not_found"
        )
    if isinstance(exc, SetupStepError):
        return APIError(
            409,
            "This setup step isn't available yet.",
            "setup_step_invalid",
            status=exc.status,
            expected=exc.expected,
        )
    return APIError(
        503,
        "No AI model is set up for this knowledge base, so setup can't use AI help. "
        "Pick a business type and continue without it.",
        "assistant_llm_unavailable",
    )


@router.post("", response_model=SetupSessionResponse, status_code=201)
async def start_setup(
    corpus_id: UUID,
    body: SetupStartRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SetupSessionResponse:
    """Start a guided setup; older unfinished sessions of this knowledge base are abandoned."""
    row = await SetupService(session, request).start(corpus, body.business_type, body.website_url)
    return _out(row, corpus, request)


@router.get("/{sid}", response_model=SetupSessionResponse)
async def get_setup(
    corpus_id: UUID,
    sid: UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SetupSessionResponse:
    """One setup session: its status, proposal, confirmed profile and answers."""
    try:
        return _out(await SetupService(session, request).get(corpus, sid), corpus, request)
    except _HANDLED as exc:
        raise _translate(exc) from None


@router.post("/{sid}/propose", response_model=SetupProposalResponse)
async def propose_setup(
    corpus_id: UUID,
    sid: UUID,
    body: SetupProposeRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SetupProposalResponse:
    """Propose type, services, hours and contacts from crawled website text (fast model)."""
    settings = request.app.state.settings
    total = sum(len(p.text) for p in body.pages)
    if len(body.pages) > settings.setup_max_pages or total > settings.setup_max_chars:
        raise RequestValidationError(
            [
                {
                    "loc": ("body", "pages"),
                    "type": "value_error",
                    "msg": f"At most {settings.setup_max_pages} pages and "
                    f"{settings.setup_max_chars} characters",
                }
            ]
        )
    pages = [PageText(url=p.url, title=p.title, text=p.text) for p in body.pages]
    try:
        row = await SetupService(session, request).propose(corpus, sid, pages, body.crawl_job_id)
    except _HANDLED as exc:
        raise _translate(exc) from None
    return SetupProposalResponse(session=_out(row, corpus, request), proposal=row.proposal or {})


@router.get("/{sid}/questions", response_model=SetupQuestionsResponse)
async def setup_questions(
    corpus_id: UUID,
    sid: UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SetupQuestionsResponse:
    """The interview questions, prefilled from the proposal, and the profile to confirm."""
    try:
        items, profile = await SetupService(session, request).questions(corpus, sid)
    except _HANDLED as exc:
        raise _translate(exc) from None
    return SetupQuestionsResponse.model_validate({"items": items, "profile": profile})


@router.post("/{sid}/answers", response_model=SetupSessionResponse)
async def setup_answers(
    corpus_id: UUID,
    sid: UUID,
    body: SetupAnswersRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SetupSessionResponse:
    """Save the interview answers and the confirmed profile."""
    profile = body.profile.model_dump(mode="json") if body.profile is not None else None
    try:
        row = await SetupService(session, request).answer(
            corpus, sid, body.business_type, body.answers, profile, partial=body.partial
        )
    except InterviewAnswerError as exc:
        raise _answer_error(exc) from None
    except _HANDLED as exc:
        raise _translate(exc) from None
    return _out(row, corpus, request)


@router.post("/{sid}/generate", response_model=SetupGenerateResponse)
async def setup_generate(
    corpus_id: UUID,
    sid: UUID,
    body: SetupGenerateRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SetupGenerateResponse:
    """Write the draft assistant from the answers; every generated item records its source."""
    try:
        row, revision, result = await SetupService(session, request).generate(
            corpus, sid, wording=body.wording, base_revision=body.base_revision
        )
    except InterviewAnswerError as exc:
        raise _answer_error(exc) from None
    except _HANDLED as exc:
        raise _translate(exc) from None
    return SetupGenerateResponse(
        session=_out(row, corpus, request),
        draft_revision=revision,
        config=row.generated_config or {},
        provenance=row.provenance,
        wording_used="ai" if result.wording_used == "ai" else "template",
        warnings=result.warnings,
    )


@router.post("/{sid}/tests", response_model=SetupTestsResponse, status_code=202)
async def setup_tests(
    corpus_id: UUID,
    sid: UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SetupTestsResponse:
    """Run the 15-25 automatic checks against the draft (a golden run in the background)."""
    try:
        row, cases = await SetupService(session, request).run_tests(corpus, sid)
    except _HANDLED as exc:
        raise _translate(exc) from None
    if row.golden_set_id is None or row.eval_run_id is None:  # set together with the status
        raise _translate(SetupStepError(row.status, ["generated"]))
    return SetupTestsResponse(
        session=_out(row, corpus, request),
        golden_set_id=row.golden_set_id,
        eval_run_id=row.eval_run_id,
        cases=cases,
    )


@router.get("/{sid}/gaps", response_model=SetupGapsResponse)
async def setup_gaps(
    corpus_id: UUID,
    sid: UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SetupGapsResponse:
    """What the checks could not find in the documents (empty while the run is unfinished)."""
    try:
        status, items = await SetupService(session, request).gaps(corpus, sid)
    except _HANDLED as exc:
        raise _translate(exc) from None
    return SetupGapsResponse.model_validate({"run_status": status, "items": items})


@router.post("/{sid}/apply", response_model=SetupApplyResponse)
async def setup_apply(
    corpus_id: UUID,
    sid: UUID,
    body: SetupApplyRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> SetupApplyResponse:
    """Publish the generated draft (owner only)."""
    try:
        row, version = await SetupService(session, request).apply(
            corpus, sid, base_revision=body.base_revision, note=body.note
        )
    except _HANDLED as exc:
        raise _translate(exc) from None
    return SetupApplyResponse(session=_out(row, corpus, request), version=version)
