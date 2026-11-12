"""Guided setup service (C8): the state machine over a fake database."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.setup.propose import PageText, SetupLLMUnavailableError
from callobuzz_rag.conversation.setup.service import (
    SetupNotFoundError,
    SetupService,
    SetupStepError,
)
from callobuzz_rag.conversation.store import AssistantConfigStore
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.assistant_setup import AssistantSetupSession
from callobuzz_rag.db.models.corpus import Corpus
from tests.unit.accounts_fakes import FakeDB, request_stub

ANSWERS = {
    "audience": "patients",
    "tone": "warm",
    "languages": ["en", "hi-Latn"],
    "when_unsure": "whatsapp",
    "top_services": ["Blood tests", "Health packages"],
    "lead_followup": "same_day",
    "sensitive_profile": "none",
    "assistant_name": "Asha",
    "greeting_style": "short_welcome",
}
PROFILE = {"business_name": "Shree Labs", "phone": "+919822012345", "whatsapp": "+919822012345"}


def _world(encryption: bool = True) -> tuple[FakeDB, Corpus, SetupService, Any, Settings]:
    db = FakeDB()
    corpus = db.add(
        Corpus(name="Help", tenant_id=uuid.uuid4(), qdrant_collection="c_x", api_key_hash="")
    )
    key = Fernet.generate_key().decode() if encryption else ""
    settings = Settings(_env_file=None, encryption_key=key)
    request = request_stub(settings)
    request.state.is_admin = True
    service = SetupService(db.session(), request)  # type: ignore[arg-type]
    return db, corpus, service, request, settings


async def test_start_abandons_older_open_sessions() -> None:
    _db, corpus, service, *_ = _world()
    first = await service.start(corpus, "clinic", None)
    second = await service.start(corpus, None, "https://shreelabs.in")
    assert first.status == "abandoned" and second.status == "started"
    assert second.answers == {} and second.provenance == {}
    assert second.created_by == "admin key"


async def test_a_session_of_another_knowledge_base_is_not_found() -> None:
    db, corpus, service, *_ = _world()
    row = await service.start(corpus, None, None)
    other = db.add(
        Corpus(name="Other", tenant_id=corpus.tenant_id, qdrant_collection="c_y", api_key_hash="")
    )
    with pytest.raises(SetupNotFoundError):
        await service.get(other, row.id)


async def test_questions_prefill_the_profile_from_the_proposal() -> None:
    _db, corpus, service, *_ = _world()
    row = await service.start(corpus, "clinic", "https://shreelabs.in")
    row.proposal = {
        "business_name": "Shree Labs",
        "phone": "+919822012345",
        "services": ["CBC"],
        "hours": [{"day": "fri", "open": "18:00", "close": "02:00"}],
    }
    items, profile = await service.questions(corpus, row.id)
    by_id = {i["id"]: i for i in items}
    assert by_id["top_services"]["prefill"] == ["CBC"]
    assert by_id["sensitive_profile"]["skipped_reason"] == "template"
    assert profile["business_name"] == "Shree Labs" and profile["website"] == "https://shreelabs.in"
    # what the owner confirmed wins over the crawl
    await service.answer(corpus, row.id, None, ANSWERS, {"business_name": "Shree Diagnostics"})
    _items, profile = await service.questions(corpus, row.id)
    assert profile == {"business_name": "Shree Diagnostics"}


async def test_answers_are_validated_and_the_status_moves() -> None:
    from callobuzz_rag.conversation.setup.interview import InterviewAnswerError

    _db, corpus, service, *_ = _world()
    row = await service.start(corpus, "store", None)
    with pytest.raises(InterviewAnswerError):
        await service.answer(corpus, row.id, None, {"tone": "sarcastic"}, None)
    saved = await service.answer(corpus, row.id, None, ANSWERS, PROFILE)
    assert saved.status == "interviewing" and saved.profile == PROFILE
    assert saved.answers["tone"] == "warm"


async def test_steps_out_of_order_are_refused() -> None:
    _db, corpus, service, *_ = _world()
    row = await service.start(corpus, "store", None)
    with pytest.raises(SetupStepError) as err:
        await service.run_tests(corpus, row.id)
    assert err.value.status == "started" and "generated" in err.value.expected
    with pytest.raises(SetupStepError):
        await service.generate(corpus, row.id, wording="template", base_revision=None)
    with pytest.raises(SetupStepError):
        await service.apply(corpus, row.id, base_revision=1, note=None)
    with pytest.raises(SetupStepError):
        await service.gaps(corpus, row.id)


async def test_propose_meters_tokens_as_setup_without_counting_a_question() -> None:
    _db, corpus, service, *_ = _world()
    row = await service.start(corpus, None, None)
    llm = MagicMock()
    resolved = MagicMock(model="fast-m", payer="byok")
    cost = object()
    with (
        patch.object(service, "_llm", return_value=(llm, resolved)),
        patch(
            "callobuzz_rag.conversation.setup.service.propose_from_pages",
            AsyncMock(return_value={"business_type": "clinic", "read_ok": True}),
        ),
        patch("callobuzz_rag.analytics.usage.record_usage", AsyncMock()) as meter,
        patch("callobuzz_rag.llm.cost.request_cost_of", return_value=cost),
    ):
        done = await service.propose(
            corpus, row.id, [PageText(url="https://x.in", title="t", text="x")], "job-1"
        )
    assert done.status == "proposed" and done.business_type == "clinic"
    kwargs = meter.await_args.kwargs
    assert kwargs["source"] == "setup" and kwargs["requests"] == 0
    assert kwargs["cost"] is cost and kwargs["payer"] == "byok" and kwargs["commit"] is False


async def test_propose_without_a_key_is_a_clean_error() -> None:
    _db, corpus, service, *_ = _world()
    row = await service.start(corpus, None, None)
    llm = MagicMock(has_credentials=False)
    with (
        patch.object(service, "_llm", return_value=(llm, MagicMock())),
        pytest.raises(SetupLLMUnavailableError),
    ):
        await service.propose(
            corpus, row.id, [PageText(url="https://x.in", title="t", text="x")], None
        )


async def test_generate_saves_the_draft_and_keeps_the_owners_switch() -> None:
    db, corpus, service, request, settings = _world()
    store = AssistantConfigStore(db.session(), request, settings)  # type: ignore[arg-type]
    owner = AssistantConfig.model_validate(
        {"enabled": False, "leads": {"enabled": False}, "advanced": {"extra_instructions": "Hi"}}
    )
    await store.publish_initial(corpus, owner, "seed")
    row = await service.start(corpus, "clinic", None)
    await service.answer(corpus, row.id, None, ANSWERS, PROFILE)
    done, revision, result = await service.generate(
        corpus, row.id, wording="template", base_revision=None
    )
    assert done.status == "generated" and revision == 1
    assert done.generated_config is not None
    cfg = done.generated_config
    assert cfg["enabled"] is False and cfg["advanced"]["extra_instructions"] == "Hi"
    assert cfg["identity"]["business_name"] == "Shree Labs"
    assert done.provenance["identity.business_name"] == "confirmed"
    assert result.wording_used == "template"
    # a second generate replaces the first draft it wrote; it does not pile up
    again, revision, _ = await service.generate(
        corpus, row.id, wording="template", base_revision=None
    )
    assert revision == 2 and again.generated_config == cfg
    # an explicit stale revision is a conflict, as everywhere else
    with pytest.raises(APIError) as err:
        await service.generate(corpus, row.id, wording="template", base_revision=1)
    assert err.value.code == "assistant_draft_conflict"


async def test_generate_without_a_key_asks_for_template_wording() -> None:
    _db, corpus, service, *_ = _world()
    row = await service.start(corpus, "clinic", None)
    await service.answer(corpus, row.id, None, ANSWERS, PROFILE)
    with (
        patch.object(service, "_llm", return_value=(MagicMock(has_credentials=False), None)),
        pytest.raises(SetupLLMUnavailableError),
    ):
        await service.generate(corpus, row.id, wording="ai", base_revision=None)


async def test_generate_without_encryption_keeps_leads_off_and_still_publishes() -> None:
    _db, corpus, service, *_ = _world(encryption=False)
    row = await service.start(corpus, "clinic", None)
    await service.answer(corpus, row.id, None, ANSWERS, PROFILE)
    done, _rev, result = await service.generate(
        corpus, row.id, wording="template", base_revision=None
    )
    assert done.generated_config is not None
    assert done.generated_config["leads"]["enabled"] is False
    assert "leads_need_encryption_key" in result.warnings


async def test_generate_fails_early_on_a_config_that_cannot_publish() -> None:
    _db, corpus, service, *_ = _world()
    row = await service.start(corpus, "clinic", None)
    await service.answer(corpus, row.id, None, ANSWERS, PROFILE)
    problem = APIError(422, "too many", "assistant_rule_limit")
    with (
        patch("callobuzz_rag.conversation.store.publish_problems", side_effect=problem),
        pytest.raises(APIError) as err,
    ):
        await service.generate(corpus, row.id, wording="template", base_revision=None)
    assert err.value.code == "assistant_rule_limit"
    assert row.status == "interviewing"  # nothing was saved


async def test_run_tests_uses_a_fast_setup_run_on_the_draft() -> None:
    _db, corpus, service, *_ = _world()
    row = await service.start(corpus, "clinic", None)
    await service.answer(corpus, row.id, None, ANSWERS, PROFILE)
    await service.generate(corpus, row.id, wording="template", base_revision=None)
    golden_set = MagicMock(id=uuid.uuid4())
    run = MagicMock(id=uuid.uuid4())
    create = AsyncMock(return_value=golden_set)
    start = AsyncMock(return_value=run)
    with (
        patch(
            "callobuzz_rag.conversation.setup.service.section_headings", AsyncMock(return_value=[])
        ),
        patch("callobuzz_rag.evaluation.golden_runner.create_golden_set", create),
        patch("callobuzz_rag.evaluation.golden_runner.start_golden_run", start),
    ):
        done, cases = await service.run_tests(corpus, row.id)
    assert done.status == "tested" and done.golden_set_id == golden_set.id
    assert done.eval_run_id == run.id and 15 <= cases <= 25
    assert create.await_args.kwargs["source"] == "setup"
    assert len(create.await_args.kwargs["pairs"]) == cases
    assert start.await_args.kwargs["mode"] == "fast"
    assert start.await_args.kwargs["triggered_by"] == "setup"
    assert start.await_args.kwargs["assistant"] == "draft"


async def test_gaps_wait_for_the_run() -> None:
    _db, corpus, service, *_ = _world()
    row = await service.start(corpus, "clinic", None)
    row.eval_run_id = uuid.uuid4()
    run = MagicMock(status="running")
    with patch.object(service._s, "get", AsyncMock(return_value=run)):
        assert await service.gaps(corpus, row.id) == ("running", [])
    assert isinstance(row, AssistantSetupSession)


async def test_progress_can_be_saved_but_generate_needs_every_required_answer() -> None:
    from callobuzz_rag.conversation.setup.interview import InterviewAnswerError

    _db, corpus, service, *_ = _world()
    row = await service.start(corpus, "store", None)
    saved = await service.answer(corpus, row.id, None, {"tone": "warm"}, PROFILE, partial=True)
    assert saved.status == "interviewing" and saved.answers == {"tone": "warm"}
    with pytest.raises(InterviewAnswerError) as err:
        await service.generate(corpus, row.id, wording="template", base_revision=None)
    assert err.value.reason == "required"
    assert saved.status == "interviewing"
    await service.answer(corpus, row.id, None, ANSWERS, None)
    done, _rev, _result = await service.generate(
        corpus, row.id, wording="template", base_revision=None
    )
    assert done.status == "generated" and done.profile == PROFILE


async def test_run_tests_twice_keeps_one_setup_set() -> None:
    from callobuzz_rag.db.models.golden_set import GoldenSet

    db, corpus, service, *_ = _world()
    row = await service.start(corpus, "clinic", None)
    await service.answer(corpus, row.id, None, ANSWERS, PROFILE)
    await service.generate(corpus, row.id, wording="template", base_revision=None)
    golden_set = db.add(
        GoldenSet(id=uuid.uuid4(), corpus_id=corpus.id, name="Guided setup checks", source="setup")
    )
    create = AsyncMock(return_value=golden_set)
    start = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    real_execute = service._s.execute
    deletes: list[Any] = []

    async def execute(stmt: Any) -> Any:
        if stmt.__visit_name__ == "delete":
            deletes.append(stmt)
            return MagicMock()
        return await real_execute(stmt)

    with (
        patch(
            "callobuzz_rag.conversation.setup.service.section_headings", AsyncMock(return_value=[])
        ),
        patch("callobuzz_rag.evaluation.golden_runner.create_golden_set", create),
        patch("callobuzz_rag.evaluation.golden_runner.start_golden_run", start),
        patch.object(service._s, "execute", execute),
    ):
        await service.run_tests(corpus, row.id)  # first: creates (no set yet)
        await service.run_tests(corpus, row.id)  # second: replaces that set's pairs
    assert create.await_count == 1
    assert len(deletes) == 1
    assert row.golden_set_id == golden_set.id


async def test_start_drops_the_setup_set_of_an_abandoned_session() -> None:
    from callobuzz_rag.db.models.golden_set import GoldenSet

    db, corpus, service, *_ = _world()
    first = await service.start(corpus, "clinic", None)
    stale = db.add(GoldenSet(id=uuid.uuid4(), corpus_id=corpus.id, name="x", source="setup"))
    first.golden_set_id = stale.id
    await service.start(corpus, None, None)
    assert first.status == "abandoned" and first.golden_set_id is None
    assert stale not in db.all(GoldenSet)


async def test_generate_records_the_wording_spend_even_when_the_checks_refuse() -> None:
    db, corpus, service, *_ = _world()
    row = await service.start(corpus, "clinic", None)
    await service.answer(corpus, row.id, None, ANSWERS, PROFILE)
    llm = MagicMock(has_credentials=True)
    problem = APIError(422, "too many", "assistant_rule_limit")
    before = db.commits
    with (
        patch.object(service, "_llm", return_value=(llm, MagicMock())),
        patch(
            "callobuzz_rag.conversation.setup.service.generate_config",
            AsyncMock(return_value=MagicMock(config=MagicMock())),
        ),
        patch.object(service, "_meter", AsyncMock()) as meter,
        patch("callobuzz_rag.conversation.store.publish_problems", side_effect=problem),
        pytest.raises(APIError),
    ):
        await service.generate(corpus, row.id, wording="ai", base_revision=None)
    meter.assert_awaited_once()
    assert db.commits == before + 1
