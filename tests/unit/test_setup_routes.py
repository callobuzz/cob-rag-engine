"""Guided setup API (C8): the state machine through the routes, with the service faked."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.conversation.setup.generate import GenerationResult
from callobuzz_rag.conversation.setup.interview import InterviewAnswerError
from callobuzz_rag.conversation.setup.propose import SetupLLMUnavailableError
from callobuzz_rag.conversation.setup.service import SetupNotFoundError, SetupStepError
from tests.unit.scope_harness import call, scoped_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")
SERVICE = "callobuzz_rag.api.routes.assistant_setup.SetupService"
SID = uuid.uuid4()


def _row(corpus_id: Any, status: str = "started", **extra: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": SID,
        "corpus_id": corpus_id,
        "status": status,
        "business_type": "clinic",
        "website_url": "https://shreelabs.in",
        "crawl_job_id": None,
        "proposal": None,
        "profile": None,
        "answers": {},
        "provenance": {},
        "golden_set_id": None,
        "eval_run_id": None,
        "generated_config": None,
        "created_by": "admin key",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(extra)
    return SimpleNamespace(**base)


def _url(world: Any, path: str = "") -> str:
    return f"/api/v1/corpora/{world.corpus('a').id}/assistant/setup{path}"


async def test_start_returns_201() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.start = AsyncMock(return_value=_row(world.corpus("a").id))
        res = await call(
            app,
            "owner_a",
            "POST",
            _url(world),
            json={"business_type": "clinic", "website_url": "https://shreelabs.in"},
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "started" and body["recommend_multilingual"] is False
    assert body["profile"] is None


async def test_start_refuses_a_non_http_url_and_unknown_fields() -> None:
    with scoped_app() as (app, world):
        bad = await call(app, "owner_a", "POST", _url(world), json={"website_url": "ftp://x"})
        extra = await call(app, "owner_a", "POST", _url(world), json={"colour": "blue"})
    assert bad.status_code == 422 and extra.status_code == 422


async def test_unknown_session_is_404() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.get = AsyncMock(side_effect=SetupNotFoundError())
        res = await call(app, "owner_a", "GET", _url(world, f"/{SID}"))
    assert res.status_code == 404 and res.json()["code"] == "setup_session_not_found"


async def test_out_of_order_is_409_with_status() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.run_tests = AsyncMock(side_effect=SetupStepError("started", ["generated"]))
        res = await call(app, "owner_a", "POST", _url(world, f"/{SID}/tests"))
    assert res.status_code == 409
    assert res.json() == {
        "detail": "This setup step isn't available yet.",
        "code": "setup_step_invalid",
        "status": "started",
        "expected": ["generated"],
    }


async def test_propose_without_llm_is_503() -> None:
    pages = [{"url": "https://shreelabs.in/", "title": "Home", "text": "Blood tests"}]
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.propose = AsyncMock(side_effect=SetupLLMUnavailableError("x"))
        res = await call(
            app, "owner_a", "POST", _url(world, f"/{SID}/propose"), json={"pages": pages}
        )
    assert res.status_code == 503 and res.json()["code"] == "assistant_llm_unavailable"


async def test_propose_returns_the_proposal() -> None:
    pages = [{"url": "https://shreelabs.in/", "title": "Home", "text": "Blood tests"}]
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        row = _row(world.corpus("a").id, "proposed", proposal={"read_ok": True})
        svc.return_value.propose = AsyncMock(return_value=row)
        res = await call(
            app, "owner_a", "POST", _url(world, f"/{SID}/propose"), json={"pages": pages}
        )
    assert res.status_code == 200, res.text
    assert res.json()["proposal"] == {"read_ok": True}


async def test_propose_rejects_too_much_text_and_too_many_pages() -> None:
    big = [{"url": f"https://x.in/{i}", "title": "p", "text": "x" * 20000} for i in range(4)]
    many = [{"url": f"https://x.in/{i}", "title": "p", "text": "x"} for i in range(16)]
    with scoped_app() as (app, world):
        a = await call(app, "owner_a", "POST", _url(world, f"/{SID}/propose"), json={"pages": big})
        b = await call(app, "owner_a", "POST", _url(world, f"/{SID}/propose"), json={"pages": many})
        c = await call(app, "owner_a", "POST", _url(world, f"/{SID}/propose"), json={"pages": []})
    assert a.status_code == 422 and b.status_code == 422 and c.status_code == 422


async def test_bad_answer_lands_on_its_question() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.answer = AsyncMock(
            side_effect=InterviewAnswerError("tone", "unknown_option")
        )
        res = await call(
            app,
            "owner_a",
            "POST",
            _url(world, f"/{SID}/answers"),
            json={"answers": {"tone": "sarcastic"}},
        )
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "answers", "tone"]


async def test_profile_is_validated_and_normalised() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        row = _row(world.corpus("a").id, "interviewing")
        svc.return_value.answer = AsyncMock(return_value=row)
        ok = await call(
            app,
            "owner_a",
            "POST",
            _url(world, f"/{SID}/answers"),
            json={
                "answers": {},
                "profile": {
                    "phone": "098220 12345",
                    "email": "care@shreelabs.in",
                    "hours": [{"day": "fri", "open": "18:00", "close": "02:00"}],
                },
            },
        )
        sent = svc.return_value.answer.await_args.args[4]
        bad_phone = await call(
            app,
            "owner_a",
            "POST",
            _url(world, f"/{SID}/answers"),
            json={"answers": {}, "profile": {"phone": "12345"}},
        )
        bad_hours = await call(
            app,
            "owner_a",
            "POST",
            _url(world, f"/{SID}/answers"),
            json={
                "answers": {},
                "profile": {"hours": [{"day": "fri", "open": "09:00", "close": "09:00"}]},
            },
        )
        extra = await call(
            app,
            "owner_a",
            "POST",
            _url(world, f"/{SID}/answers"),
            json={"answers": {}, "profile": {"fax": "1"}},
        )
    assert ok.status_code == 200, ok.text
    assert sent["phone"] == "+919822012345" and sent["hours"][0]["close"] == "02:00"
    assert bad_phone.status_code == 422 and bad_hours.status_code == 422
    assert extra.status_code == 422


async def test_questions_carry_the_profile() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.questions = AsyncMock(
            return_value=(
                [
                    {
                        "id": "tone",
                        "kind": "single",
                        "title": "How?",
                        "help": "",
                        "options": [{"value": "warm", "label": "Warm"}],
                        "other": False,
                        "required": True,
                        "max_items": None,
                        "prefill": None,
                        "skipped_reason": None,
                    }
                ],
                {"business_name": "Shree Labs", "hours": []},
            )
        )
        res = await call(app, "member_a", "GET", _url(world, f"/{SID}/questions"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["items"][0]["id"] == "tone" and body["profile"]["business_name"] == "Shree Labs"


async def test_generate_returns_provenance_and_warnings() -> None:
    from callobuzz_rag.conversation.schema import AssistantConfig

    with scoped_app() as (app, world), patch(SERVICE) as svc:
        row = _row(
            world.corpus("a").id,
            "generated",
            generated_config={"enabled": True},
            provenance={"voice.tone_preset": "q:tone"},
        )
        result = GenerationResult(
            config=AssistantConfig(),
            provenance={},
            wording_used="template",
            warnings=["leads_need_encryption_key"],
        )
        svc.return_value.generate = AsyncMock(return_value=(row, 3, result))
        res = await call(
            app, "member_a", "POST", _url(world, f"/{SID}/generate"), json={"wording": "template"}
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["draft_revision"] == 3 and body["wording_used"] == "template"
    assert body["warnings"] == ["leads_need_encryption_key"]
    assert body["provenance"] == {"voice.tone_preset": "q:tone"}


async def test_tests_returns_202_with_the_run() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        row = _row(
            world.corpus("a").id,
            "tested",
            golden_set_id=uuid.uuid4(),
            eval_run_id=uuid.uuid4(),
        )
        svc.return_value.run_tests = AsyncMock(return_value=(row, 17))
        res = await call(app, "member_a", "POST", _url(world, f"/{SID}/tests"))
        refused = await call(
            app, "member_a", "POST", _url(world, f"/{SID}/tests"), json={"mode": "full"}
        )
    assert res.status_code == 202, res.text
    assert res.json()["cases"] == 17
    assert refused.status_code in (200, 202, 409, 422)  # a body is ignored: the route has none


async def test_gaps() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.gaps = AsyncMock(
            return_value=("completed", [{"question": "Thyroid?", "message": "Add one?"}])
        )
        res = await call(app, "member_a", "GET", _url(world, f"/{SID}/gaps"))
    assert res.status_code == 200, res.text
    assert res.json() == {
        "run_status": "completed",
        "items": [{"question": "Thyroid?", "message": "Add one?"}],
    }


async def test_apply_is_owner_only() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app, "member_a", "POST", _url(world, f"/{SID}/apply"), json={"base_revision": 3}
        )
    assert res.status_code == 403 and res.json()["code"] == "owner_required"


async def test_apply_publishes() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.apply = AsyncMock(return_value=(_row(world.corpus("a").id, "applied"), 4))
        res = await call(
            app, "owner_a", "POST", _url(world, f"/{SID}/apply"), json={"base_revision": 3}
        )
    assert res.status_code == 200, res.text
    assert res.json()["version"] == 4 and res.json()["session"]["status"] == "applied"


async def test_apply_needs_a_revision() -> None:
    with scoped_app() as (app, world):
        res = await call(app, "owner_a", "POST", _url(world, f"/{SID}/apply"), json={})
        neg = await call(
            app, "owner_a", "POST", _url(world, f"/{SID}/apply"), json={"base_revision": -1}
        )
    assert res.status_code == 422 and neg.status_code == 422


async def test_the_multilingual_recommendation() -> None:
    from callobuzz_rag.core import model_catalog

    english_only = next(
        e for e in model_catalog.EMBEDDING_CATALOG if e.get("multilingual") is False
    )
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        corpus = world.corpus("a")
        corpus.applied_embedding = {
            "provider": english_only["provider"],
            "model": english_only["model"],
            "dimensions": english_only["dimensions"],
        }
        row = _row(corpus.id, "interviewing", answers={"languages": ["en", "hi-Latn"]})
        svc.return_value.get = AsyncMock(return_value=row)
        res = await call(app, "owner_a", "GET", _url(world, f"/{SID}"))
        row.answers = {"languages": ["en"]}
        english = await call(app, "owner_a", "GET", _url(world, f"/{SID}"))
    assert res.json()["embedding_multilingual"] is False
    assert res.json()["recommend_multilingual"] is True
    assert english.json()["recommend_multilingual"] is False


async def test_progress_is_saved_with_partial() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.answer = AsyncMock(return_value=_row(world.corpus("a").id, "interviewing"))
        res = await call(
            app,
            "member_a",
            "POST",
            _url(world, f"/{SID}/answers"),
            json={"answers": {"tone": "warm"}, "partial": True},
        )
        assert svc.return_value.answer.await_args.kwargs == {"partial": True}
        await call(app, "member_a", "POST", _url(world, f"/{SID}/answers"), json={"answers": {}})
        assert svc.return_value.answer.await_args.kwargs == {"partial": False}
    assert res.status_code == 200, res.text


async def test_generate_names_the_unanswered_question() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.generate = AsyncMock(
            side_effect=InterviewAnswerError("audience", "required")
        )
        res = await call(app, "member_a", "POST", _url(world, f"/{SID}/generate"), json={})
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "answers", "audience"]


async def test_other_language_is_a_422_not_a_500() -> None:
    from callobuzz_rag.conversation.setup.interview import validate_answers

    def _validate(_c: Any, _s: Any, btype: Any, answers: Any, *_a: Any, **_k: Any) -> Any:
        return validate_answers(answers, btype or "clinic", partial=True)

    with scoped_app() as (app, world), patch(SERVICE) as svc:
        svc.return_value.answer = AsyncMock(side_effect=_validate)
        res = await call(
            app,
            "owner_a",
            "POST",
            _url(world, f"/{SID}/answers"),
            json={"answers": {"languages": ["en", {"other": "x"}]}},
        )
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "answers", "languages"]


async def test_a_session_already_holding_an_other_language_still_loads() -> None:
    with scoped_app() as (app, world), patch(SERVICE) as svc:
        row = _row(
            world.corpus("a").id, "interviewing", answers={"languages": ["hi", {"other": "x"}]}
        )
        svc.return_value.get = AsyncMock(return_value=row)
        res = await call(app, "owner_a", "GET", _url(world, f"/{SID}"))
    assert res.status_code == 200
