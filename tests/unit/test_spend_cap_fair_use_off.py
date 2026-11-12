"""The owner's spending cap is a billing control the owner sets, so the deployer switching fair
use off (`RAG_FAIR_USE_ENABLED=false`) must not switch it off (TV3 review)."""

from callobuzz_rag.billing.money import MICRO_PER_MINOR
from callobuzz_rag.fairuse.usage import FairUseUsage
from tests.unit.test_fairuse_gate import effective, env, request, run

CAP = 200_000  # Rs 2,000


def charged(minor: int) -> FairUseUsage:
    return FairUseUsage(spend_cap_month_micro=minor * MICRO_PER_MINOR)


async def test_the_cap_holds_with_fair_use_switched_off() -> None:
    with env(effective(spend_cap_minor=CAP), usage=charged(500_000)) as s:
        decision = await run(request(enabled=False))
    assert decision.action in ("downgrade", "pause")
    assert "spend_cap" in decision.reasons
    s.usage.assert_awaited_once()
    s.schedule.assert_called_once()  # the owner is still told it was reached


async def test_below_the_cap_everything_is_allowed_with_fair_use_off() -> None:
    with env(effective(spend_cap_minor=CAP), usage=charged(10_000)) as s:
        decision = await run(request(enabled=False))
    assert decision.action == "allow"
    s.schedule.assert_not_called()


async def test_fair_use_off_still_ignores_the_deployers_limits_and_the_engine_rail() -> None:
    """Only the owner's cap survives: the question windows are not read or counted."""
    with env(effective({"questions_per_day": 1}, spend_cap_minor=CAP), usage=charged(0)) as s:
        decision = await run(request(enabled=False))
    assert decision.action == "allow"
    assert s.usage.await_args.kwargs["metrics"] == ["spend_cap_per_month"]
    s.engine.assert_not_awaited()
    s.hit.assert_not_awaited()
