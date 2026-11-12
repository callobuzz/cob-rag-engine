"""C4's lead creation stores the bucket; the confirmation adds the booking link; the list
filters by it (C9). The webhook payload key is C4's and tested there."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from callobuzz_rag.api.routes.leads import _booking, _confirmation
from callobuzz_rag.conversation.leads.service import LeadFilters, _filter_clauses, create_lead
from callobuzz_rag.conversation.schema import AssistantConfig
from tests.unit.scope_harness import call, scoped_app
from tests.unit.test_lead_service import CRYPTO, _new
from tests.unit.test_leads_api import KEY, _configure

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
HOOK = "callobuzz_rag.api.routes.leads."


def _config(booking: str | None = None) -> AssistantConfig:
    raw = AssistantConfig().model_dump()
    raw["identity"]["card"]["booking_url"] = booking
    raw["leads"]["qualification"] = [
        {
            "status": "qualified",
            "conditions": [{"field": "intent", "op": "eq", "value": "buying_intent"}],
        }
    ]
    return AssistantConfig.model_validate(raw)


def _session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


async def test_create_lead_stores_the_bucket() -> None:
    lead = await create_lead(_session(), CRYPTO, _config(), _new(trigger="buying_intent"), now=NOW)
    assert lead.qualification == "qualified"
    lead = await create_lead(_session(), CRYPTO, _config(), _new(trigger="no_answer"), now=NOW)
    assert lead.qualification == "unqualified"


async def test_no_rules_leaves_it_null() -> None:
    lead = await create_lead(_session(), CRYPTO, AssistantConfig(), _new(), now=NOW)
    assert lead.qualification is None


def test_confirmation_carries_the_booking_link_only_when_given() -> None:
    config = _config("https://shree-labs.in/book")
    url = "https://rag.example/privacy/withdraw#t=x"
    with_link = _confirmation(config, "en", url, booking_url="https://shree-labs.in/book")
    assert with_link.booking_url == "https://shree-labs.in/book"
    assert _confirmation(config, "en", url).booking_url is None


def test_booking_link_is_for_qualified_leads_only() -> None:
    config = _config("https://shree-labs.in/book")
    link = "https://shree-labs.in/book"
    for bucket, expected in (
        ("qualified", link),
        ("partial", None),
        ("unqualified", None),
        (None, None),
    ):
        assert _booking(config, SimpleNamespace(qualification=bucket)) == expected
    assert _booking(config, None) is None
    assert _booking(_config(), SimpleNamespace(qualification="qualified")) is None


def test_filter_clause() -> None:
    clauses = _filter_clauses(CRYPTO, LeadFilters(qualification="qualified"))
    assert any("qualification" in str(c) for c in clauses)


async def test_list_route_passes_the_qualification_filter() -> None:
    assert KEY
    with scoped_app() as (app, _world):
        _configure(app)
        listing = AsyncMock(return_value=([], 0))
        with patch(HOOK + "list_leads", listing):
            res = await call(app, "tk_a", "GET", "/api/v1/leads?qualification=qualified")
            bad = await call(app, "tk_a", "GET", "/api/v1/leads?qualification=hot")
    assert res.status_code == 200, res.text
    assert listing.await_args.kwargs["filters"].qualification == "qualified"
    assert bad.status_code == 422
