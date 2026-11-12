"""TV5: Home nudges (brochures, a way to reach the business, lead value)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.api.schemas.home import HomeValue, HomeValueMonth
from callobuzz_rag.business.home import build_home
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.website.policy import WebsitePolicy

NOW = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)


def _corpus() -> Any:
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="Shree Dental Clinic")


def _scalars(rows: list[Any]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    result.scalars.return_value.first.return_value = rows[0] if rows else None
    return result


def _one(*values: int) -> MagicMock:
    result = MagicMock()
    result.one.return_value = values
    return result


def _published(**config: Any) -> Any:
    dumped = AssistantConfig.model_validate({"enabled": True, **config}).model_dump(mode="json")
    return SimpleNamespace(status="published", config=dumped)


def _site(learned: int) -> Any:
    return SimpleNamespace(
        origin="https://shreedental.in",
        status="ready",
        pages_learned=learned,
        last_run_finished_at=NOW,
    )


def _value(saved: bool) -> HomeValue:
    return HomeValue(
        month_to_date=HomeValueMonth(
            total_minor=0, currency="INR", answered=0, leads_qualified=0, handoffs=0
        ),
        saved_settings=saved,
        last_month_total_minor=None,
    )


REACHABLE = {"identity": {"card": {"phone": "+91 98220 12345"}}}
UNREACHABLE = {"handoff": {"enabled": False}, "leads": {"enabled": False}}


def _around(session: MagicMock, value: HomeValue | None) -> Any:
    """Everything Home reads that is not the nudges' own business."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "callobuzz_rag.website.policy.website_policy",
            AsyncMock(return_value=WebsitePolicy(True, None, 100)),
        )
    )
    stack.enter_context(
        patch(
            "callobuzz_rag.onboarding.service.home_onboarding",
            AsyncMock(return_value=SimpleNamespace(status="done", step=4)),
        )
    )
    stack.enter_context(
        patch("callobuzz_rag.onboarding.service.site_connected", AsyncMock(return_value=True))
    )
    stack.enter_context(
        patch("callobuzz_rag.business.home._value_card", AsyncMock(return_value=value))
    )
    return stack


async def _nudges(
    *,
    files: int = 0,
    conversations: int = 0,
    pages: int = 0,
    config: dict[str, Any] | None = None,
    value: HomeValue | None = None,
) -> tuple[list[str], MagicMock]:
    """The nudge kinds Home reports, and the session (to count the queries)."""
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            _scalars([_published(**(config or REACHABLE))]),
            _scalars([_site(pages)]),
            _one(0, 0, 0),
            _one(files, conversations),
        ]
    )
    with _around(session, value):
        out = await build_home(session, MagicMock(), _corpus(), now=NOW, tenant_id=uuid.uuid4())
    return [n.kind for n in out.nudges], session


async def test_brochures_are_suggested_with_no_file_and_few_pages() -> None:
    kinds, _ = await _nudges(files=0, pages=3)
    assert kinds == ["add_brochures"]


async def test_one_file_or_ten_pages_is_enough() -> None:
    assert (await _nudges(files=1, pages=3))[0] == []
    assert (await _nudges(files=0, pages=10))[0] == []
    assert (await _nudges(files=0, pages=9))[0] == ["add_brochures"]


async def test_no_way_to_reach_the_business_is_the_first_nudge() -> None:
    kinds, _ = await _nudges(files=0, pages=2, config=UNREACHABLE)
    assert kinds == ["no_human_path", "add_brochures"]


@pytest.mark.parametrize(
    "config",
    [
        REACHABLE,  # a phone on the business card
        {"leads": {"enabled": True}, "handoff": {"enabled": False}},  # the call-back form
        {"identity": {"card": {"email": "hello@shreedental.in"}}, "leads": {"enabled": False}},
    ],
)
async def test_a_way_to_reach_the_business_means_no_warning(config: dict[str, Any]) -> None:
    kinds, _ = await _nudges(files=1, pages=20, config=config)
    assert "no_human_path" not in kinds


async def test_lead_value_is_suggested_after_twenty_conversations_with_no_saved_values() -> None:
    kinds, _ = await _nudges(files=1, conversations=20, value=_value(saved=False))
    assert kinds == ["set_values"]
    assert (await _nudges(files=1, conversations=19, value=_value(saved=False)))[0] == []
    assert (await _nudges(files=1, conversations=40, value=_value(saved=True)))[0] == []
    # the deployer switched the value report off: nothing to set
    assert (await _nudges(files=1, conversations=40, value=None))[0] == []


async def test_all_three_come_in_a_fixed_order() -> None:
    kinds, _ = await _nudges(
        files=0, pages=0, conversations=30, config=UNREACHABLE, value=_value(saved=False)
    )
    assert kinds == ["no_human_path", "add_brochures", "set_values"]


async def test_one_extra_query_and_none_without_a_knowledge_base() -> None:
    _, session = await _nudges(files=0, pages=0)
    assert session.execute.await_count == 4  # assistant, website, today, and the nudges' one

    empty = MagicMock()
    empty.execute = AsyncMock(side_effect=AssertionError("no query without a knowledge base"))
    with (
        patch(
            "callobuzz_rag.onboarding.service.home_onboarding",
            AsyncMock(return_value=SimpleNamespace(status="new", step=1)),
        ),
        patch("callobuzz_rag.onboarding.service.site_connected", AsyncMock(return_value=False)),
    ):
        out = await build_home(empty, MagicMock(), None, now=NOW, tenant_id=uuid.uuid4())
    assert out.nudges == []


async def test_an_unreadable_published_config_never_warns() -> None:
    session = MagicMock()
    broken = SimpleNamespace(status="published", config={"identity": "nonsense"})
    session.execute = AsyncMock(
        side_effect=[_scalars([broken]), _scalars([_site(50)]), _one(0, 0, 0), _one(1, 0)]
    )
    with _around(session, None):
        out = await build_home(session, MagicMock(), _corpus(), now=NOW, tenant_id=uuid.uuid4())
    assert out.nudges == []
