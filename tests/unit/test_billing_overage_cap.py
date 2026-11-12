"""The monthly cap follows the plan's overage rule (BIL4 task 2)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts import caps
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.pricing import PricingFields, PricingOverrides, combine

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
TENANT = uuid.uuid4()


def _request() -> Any:
    return SimpleNamespace(
        state=SimpleNamespace(is_admin=False),
        app=SimpleNamespace(state=SimpleNamespace(session_factory=None)),
    )


def _env(quota: tuple[int, int] | None, *, exempt: bool = False, **fields: Any) -> Any:
    eff = combine(PricingFields(**fields), PricingOverrides(), currency="INR", exempt=exempt)
    pricing = AsyncMock(return_value=eff)
    return (
        patch.object(caps, "question_quota", AsyncMock(return_value=quota)),
        patch("callobuzz_rag.billing.pricing.effective_pricing", pricing),
        pricing,
    )


async def _enforce(quota: tuple[int, int] | None, payer: str | None = None, **kw: Any) -> Any:
    exempt = kw.pop("exempt", False)
    q, p, pricing = _env(quota, exempt=exempt, **kw)
    with q, p:
        await caps.enforce_question_cap(AsyncMock(), _request(), TENANT, now=NOW, payer=payer)
    return pricing


async def test_block_is_todays_429() -> None:
    with pytest.raises(APIError) as err:
        await _enforce((10, 10), overage="block")
    assert err.value.status_code == 429 and err.value.code == "question_cap_reached"


@pytest.mark.parametrize("overage", ["charge", "allow"])
async def test_charge_and_allow_let_the_question_through(overage: str) -> None:
    await _enforce((10, 50), overage=overage)


async def test_byok_not_counted_is_never_capped() -> None:
    await _enforce((10, 50), payer="byok", byok_counts_toward_caps=False, overage="block")
    with pytest.raises(APIError):
        await _enforce((10, 50), payer="byok", byok_counts_toward_caps=True, overage="block")
    with pytest.raises(APIError):  # the operator's key still counts
        await _enforce((10, 50), payer=None, byok_counts_toward_caps=False, overage="block")


async def test_below_the_cap_reads_no_pricing() -> None:
    pricing = await _enforce((10, 9), overage="allow")
    pricing.assert_not_awaited()


async def test_exempt_workspace_keeps_block() -> None:
    with pytest.raises(APIError):
        await _enforce((10, 10), exempt=True, overage="allow")


async def test_cap_blocks_answers_matches_the_exception() -> None:
    for fields, payer, expect in [
        ({"overage": "block"}, None, True),
        ({"overage": "charge"}, None, False),
        ({"overage": "allow"}, "operator", False),
        ({"overage": "block", "byok_counts_toward_caps": False}, "byok", False),
    ]:
        q, p, _ = _env((10, 10), **fields)
        with q, p:
            assert await caps.cap_blocks_answers(AsyncMock(), TENANT, NOW, payer) is expect
    q, p, _ = _env(None)
    with q, p:
        assert await caps.cap_blocks_answers(AsyncMock(), TENANT, NOW) is False
