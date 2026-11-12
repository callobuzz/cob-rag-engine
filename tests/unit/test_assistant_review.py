"""Review inbox: grouping, hiding acted-on items, and actions that write the draft."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.assistant_quality import ReviewActionRequest
from callobuzz_rag.conversation import review
from callobuzz_rag.conversation.review import ReviewTurn, build_items, group_key, item_key
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.db.models import AssistantTurn

NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


def turn(kind: str, question: str, minutes: int, topic: str | None = None) -> ReviewTurn:
    group = group_key(topic, question) if kind in ("no_answer", "low_confidence") else question
    return ReviewTurn(
        id=uuid.uuid4(),
        kind=kind,
        group=group,
        title=question,
        question=question,
        at=NOW - timedelta(minutes=minutes),
        conversation_id=None,
        topic_id=topic,
    )


def test_group_key_uses_topic_first() -> None:
    assert group_key("home-collection", "anything") == "topic:home-collection"


def test_group_key_from_keywords_is_order_free() -> None:
    a = group_key(None, "Is home collection free in Kothrud?")
    b = group_key(None, "home collection kothrud free??")
    assert a == b and a.startswith("terms:")


def test_item_key_is_stable_and_32_chars() -> None:
    assert item_key("no_answer", "topic:x") == item_key("no_answer", "topic:x")
    assert len(item_key("no_answer", "topic:x")) == 32
    assert item_key("no_answer", "topic:x") != item_key("low_confidence", "topic:x")


def test_items_group_and_sort_by_count() -> None:
    rows = [
        turn("no_answer", "Is home collection free in Kothrud?", 5),
        turn("no_answer", "home collection kothrud free", 50),
        turn("no_answer", "Do you do MRI scans?", 10),
    ]
    items = build_items(rows, {})
    assert [i.count for i in items] == [2, 1]
    assert items[0].last_at == NOW - timedelta(minutes=5)
    assert items[0].suggested == ["added_answer", "added_document", "added_rule", "dismissed"]
    assert len(items[0].examples) == 2 and items[0].examples[0].at > items[0].examples[1].at


def test_examples_are_capped_at_five() -> None:
    rows = [turn("no_answer", "Do you do MRI scans?", m) for m in range(8)]
    (item,) = build_items(rows, {})
    assert item.count == 8 and len(item.examples) == 5


def test_acted_items_hide_until_the_problem_returns() -> None:
    rows = [turn("no_answer", "Do you do MRI scans?", 30)]
    key = build_items(rows, {})[0].item_key
    assert build_items(rows, {key: NOW - timedelta(minutes=10)}) == []
    newer = [*rows, turn("no_answer", "do you do mri scan", 1)]
    assert len(build_items(newer, {key: NOW - timedelta(minutes=10)})) == 1


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_Rows":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def _turn_row(**kw: Any) -> AssistantTurn:
    base: dict[str, Any] = dict(
        id=uuid.uuid4(),
        corpus_id=uuid.uuid4(),
        question_masked="Do you do MRI scans?",
        created_at=NOW,
        conversation_id=None,
        topic_id=None,
        no_answer=False,
        label_source="rule",
        label_confidence=0.9,
        gate_passed=True,
        top_score=0.9,
        timings={},
    )
    base.update(kw)
    return AssistantTurn(**base)


async def test_what_counts_as_a_review_turn() -> None:
    near_miss = _turn_row(top_score=0.43, timings={"gate_threshold": 0.4})
    comfortable = _turn_row(top_score=0.9, timings={"gate_threshold": 0.4})
    shaky = _turn_row(label_source="model", label_confidence=0.0, gate_passed=None, top_score=None)
    unanswered = _turn_row(no_answer=True, gate_passed=False)
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[_Rows([near_miss, comfortable, shaky, unanswered]), _Rows([])]
    )
    rows = await review._rows(session, uuid.uuid4(), NOW - timedelta(days=1), NOW)
    kinds = {r.id: r.kind for r in rows}
    assert kinds == {
        near_miss.id: "low_confidence",
        shaky.id: "low_confidence",
        unanswered.id: "no_answer",
    }


async def test_a_zero_label_confidence_is_shaky_not_confident() -> None:
    zero = _turn_row(label_source="model", label_confidence=0.0, gate_passed=None, top_score=None)
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[_Rows([zero]), _Rows([])])
    rows = await review._rows(session, uuid.uuid4(), NOW - timedelta(days=1), NOW)
    assert [r.kind for r in rows] == ["low_confidence"]


async def test_thumbs_down_turns_are_their_own_items() -> None:
    disliked = _turn_row()
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[_Rows([]), _Rows([disliked])])
    rows = await review._rows(session, uuid.uuid4(), NOW - timedelta(days=1), NOW)
    assert [(r.kind, r.group) for r in rows] == [("thumbs_down", f"turn:{disliked.id}")]


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(is_admin=True),
        app=SimpleNamespace(state=SimpleNamespace(settings=None)),
    )


def _action(**kw: Any) -> ReviewActionRequest:
    return ReviewActionRequest.model_validate({"item_key": "k" * 32, **kw})


async def _apply(body: ReviewActionRequest, config: AssistantConfig | None = None):
    session = AsyncMock()
    session.add = MagicMock()
    store = MagicMock()
    store.save_draft = AsyncMock(return_value=SimpleNamespace(draft_revision=4))
    runtime = SimpleNamespace(
        config=config or AssistantConfig(), version=None, draft_revision=3, enabled=True
    )
    corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    with (
        patch.object(review, "_store", return_value=store),
        patch.object(review, "get_runtime_config", AsyncMock(return_value=runtime)),
        patch.object(review, "_known_keys", AsyncMock(return_value={"k" * 32: "no_answer"})),
    ):
        out = await review.apply_review_action(session, _request(), corpus, body)
    return out, store, session, corpus


async def test_added_answer_writes_a_topic_to_the_draft() -> None:
    out, store, session, corpus = await _apply(
        _action(
            action="added_answer",
            base_revision=3,
            answer={
                "topic_name": "MRI scans",
                "question": "Do you do MRI scans?",
                "answer": "No, we are a pathology lab; we do not do MRI scans.",
            },
        )
    )
    assert out.draft_revision == 4 and out.target == "mri-scans"
    saved = store.save_draft.await_args.args
    assert saved[0] is corpus and saved[2] == 3
    topic = saved[1].scope.topics[-1]
    assert topic.id == "mri-scans"
    assert topic.verbatim_answer == {"en": "No, we are a pathology lab; we do not do MRI scans."}
    recorded = session.add.call_args.args[0]
    assert (recorded.action, recorded.kind, recorded.target) == (
        "added_answer",
        "no_answer",
        "mri-scans",
    )
    session.commit.assert_awaited_once()


async def test_a_second_answer_with_the_same_topic_name_gets_a_new_id() -> None:
    config = AssistantConfig()
    first, store, *_ = await _apply(
        _action(
            action="added_answer",
            base_revision=3,
            answer={"topic_name": "MRI", "question": "q", "answer": "a"},
        ),
        config,
    )
    config = store.save_draft.await_args.args[1]
    second, *_ = await _apply(
        _action(
            action="added_answer",
            base_revision=4,
            answer={"topic_name": "MRI", "question": "q2", "answer": "a2"},
        ),
        config,
    )
    assert (first.target, second.target) == ("mri", "mri-2")


async def test_added_rule_writes_a_guidance_rule() -> None:
    out, store, *_ = await _apply(
        _action(
            action="added_rule",
            base_revision=3,
            rule={"instruction": "Never promise a report time", "type": "compliance"},
        )
    )
    rule = store.save_draft.await_args.args[1].rules[-1]
    assert rule.instruction == "Never promise a report time" and rule.source == "owner"
    assert out.target == rule.id


async def test_dismissing_touches_no_draft() -> None:
    out, store, session, _ = await _apply(_action(action="dismissed"))
    store.save_draft.assert_not_awaited()
    assert out.draft_revision is None and out.target is None
    session.commit.assert_awaited_once()


async def test_missing_base_revision_or_payload_is_a_422() -> None:
    with pytest.raises(APIError) as no_rev:
        await _apply(_action(action="added_rule", rule={"instruction": "x", "type": "context"}))
    assert no_rev.value.status_code == 422
    with pytest.raises(APIError) as no_payload:
        await _apply(_action(action="added_answer", base_revision=0))
    assert (
        no_payload.value.status_code == 422 and no_payload.value.code == "review_payload_required"
    )


async def test_a_change_the_assistant_cannot_hold_is_a_coded_422() -> None:
    full = AssistantConfig.model_validate(
        {
            "scope": {
                "topics": [
                    {"id": f"t{i}", "name": f"Topic {i}", "description": "d"} for i in range(40)
                ]
            }
        }
    )
    with pytest.raises(APIError) as exc:
        await _apply(
            _action(
                action="added_answer",
                base_revision=3,
                answer={"topic_name": "One more", "question": "q", "answer": "a"},
            ),
            full,
        )
    assert exc.value.status_code == 422 and exc.value.code == "assistant_reference_invalid"


async def test_the_answer_language_must_be_a_known_one() -> None:
    with pytest.raises(ValueError):
        _action(
            action="added_answer",
            base_revision=0,
            answer={"topic_name": "x", "question": "q", "answer": "a", "language": "klingon"},
        )


async def test_unknown_item_is_404() -> None:
    with (
        patch.object(review, "_known_keys", AsyncMock(return_value={})),
        pytest.raises(APIError) as exc,
    ):
        await review.apply_review_action(
            AsyncMock(),
            SimpleNamespace(),
            SimpleNamespace(id=uuid.uuid4()),
            ReviewActionRequest(item_key="z" * 32, action="dismissed"),
        )
    assert exc.value.code == "review_item_not_found"


async def test_near_misses_are_chosen_in_the_query_not_after_the_row_cap() -> None:
    from sqlalchemy.dialects import postgresql

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[_Rows([]), _Rows([])])
    await review._rows(session, uuid.uuid4(), NOW - timedelta(days=1), NOW)
    turns_sql = str(
        session.execute.await_args_list[0].args[0].compile(dialect=postgresql.dialect())
    )
    # Grounded answers well above the gate must not fill the capped result.
    assert "AS FLOAT) < %(param_1)s" in turns_sql
    thumbs_sql = str(
        session.execute.await_args_list[1].args[0].compile(dialect=postgresql.dialect())
    )
    assert "ORDER BY assistant_turns.created_at DESC" in thumbs_sql
