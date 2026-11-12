"""Seed the plans and add-ons of a hosted service (P5).

    python -m callobuzz_rag.billing.seed_saas            # a dry run: what would be made
    python -m callobuzz_rag.billing.seed_saas --apply    # make it
    python -m callobuzz_rag.billing.seed_saas --file my-plans.json

Free, Standard and Pro (with their pricing), an own-AI-key add-on, three services and three
credit packs, from `billing/data/saas_seed.json`: **placeholder amounts**, there so the shape is
ready; set your own in the plan editor and the add-on catalogue. Every item goes through the same
request models and functions as the admin API, so nothing is made that the API would refuse.

Safe to run again: an item is matched by name, and one that already exists is reported ("exists,
left as it is") and never changed, so a price you edited stays. A pricing version made here is
marked `created_by = "seed:placeholder"` and an add-on `placeholder = true` until its price is
edited, which is what the "Placeholder price" badge in the dashboard reads. Exit 0 when done, 1
when something could not be made (nothing is half-made: each item is its own transaction).
"""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts import plans as plan_service
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.billing import PlanPricingRequest
from callobuzz_rag.api.schemas.billing_addons import CreateAddOnRequest
from callobuzz_rag.api.schemas.plan import CreatePlanRequest
from callobuzz_rag.billing import addons, notice
from callobuzz_rag.billing.pricing import PricingFields
from callobuzz_rag.db.models.add_on import AddOn
from callobuzz_rag.db.models.plan import Plan

logger = logging.getLogger(__name__)

SEED_FILE: Final = Path(__file__).parent / "data" / "saas_seed.json"
PLACEHOLDER_BY: Final = "seed:placeholder"
EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXISTS: Final = "exists, left as it is"


@dataclass(frozen=True)
class SeedPlan:
    name: str
    plan: CreatePlanRequest
    pricing: PricingFields


@dataclass(frozen=True)
class Seed:
    plans: list[SeedPlan]
    add_ons: list[CreateAddOnRequest]


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    made: int = 0
    left: int = 0
    failed: int = 0

    def add(self, line: str) -> None:
        self.lines.append(line)


def load_seed(path: Path | None = None) -> dict[str, Any]:
    """The raw seed file."""
    data = json.loads((path or SEED_FILE).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("The seed file must hold one JSON object.")
    return data


def parse_seed(data: dict[str, Any]) -> Seed:
    """The seed through the admin API's own request models.

    Raises:
        pydantic.ValidationError: an item the API would refuse.
    """
    plans = []
    for item in data.get("plans", []):
        plan = CreatePlanRequest.model_validate(item["plan"])
        pricing_request = PlanPricingRequest.model_validate(item["pricing"])
        pricing = PricingFields.model_validate(
            pricing_request.model_dump(exclude={"apply", "effective_at"})
        )
        plans.append(SeedPlan(plan.name, plan, pricing))
    add_ons = [
        CreateAddOnRequest.model_validate({k: v for k, v in raw.items() if k != "placeholder"})
        for raw in data.get("add_ons", [])
    ]
    return Seed(plans, add_ons)


async def _plan_exists(session: AsyncSession, name: str) -> bool:
    return (await session.execute(select(Plan.id).where(Plan.name == name))).first() is not None


async def _add_on_exists(session: AsyncSession, name: str) -> bool:
    return (await session.execute(select(AddOn.id).where(AddOn.name == name))).first() is not None


async def _make_plan(
    session: AsyncSession, item: SeedPlan, *, notice_days: int, now: datetime
) -> None:
    plan = await plan_service.create_plan(session, item.plan.model_dump(), request=None)
    await notice.save_with_notice(
        session,
        plan,
        item.pricing,
        created_by=PLACEHOLDER_BY,
        apply="now",
        effective_at=None,
        notice_days=notice_days,
        now=now,
    )


async def _make_add_on(
    session: AsyncSession, body: CreateAddOnRequest, *, placeholder: bool
) -> None:
    row = await addons.create_add_on(session, body, request=None)
    row.placeholder = placeholder


def _summary(item: SeedPlan) -> str:
    fee, credit = item.pricing.subscription_fee_minor, item.pricing.included_credit_minor
    return f"fee {fee / 100:,.2f} a month, credit {credit / 100:,.2f}"


async def run(
    session_factory: Any,
    data: dict[str, Any],
    *,
    apply: bool,
    notice_days: int = 7,
    now: datetime | None = None,
) -> Report:
    """Make (or, without `apply`, only list) what the seed holds. Items are independent: one
    that fails is reported and the rest go on."""
    seed = parse_seed(data)
    moment = now or datetime.now(UTC)
    report = Report()
    placeholder_names = {raw["name"] for raw in data.get("add_ons", []) if raw.get("placeholder")}
    for plan_item in seed.plans:
        async with session_factory() as session:
            if await _plan_exists(session, plan_item.name):
                report.left += 1
                report.add(
                    f"plan    {plan_item.name}: {EXISTS} "
                    f"(the seed would use {_summary(plan_item)}; set them in the plan editor)"
                )
                continue
            if not apply:
                report.made += 1
                report.add(f"plan    {plan_item.name}: would create ({_summary(plan_item)})")
                continue
            try:
                await _make_plan(session, plan_item, notice_days=notice_days, now=moment)
                await session.commit()
                report.made += 1
                report.add(f"plan    {plan_item.name}: created ({_summary(plan_item)})")
            except (APIError, ValueError) as exc:
                await session.rollback()
                report.failed += 1
                report.add(f"plan    {plan_item.name}: FAILED ({getattr(exc, 'detail', exc)})")
    for body in seed.add_ons:
        async with session_factory() as session:
            if await _add_on_exists(session, body.name):
                report.left += 1
                report.add(f"add-on  {body.name}: {EXISTS}")
                continue
            label = f"{body.kind}, {body.price_minor / 100:,.2f}"
            if not apply:
                report.made += 1
                report.add(f"add-on  {body.name}: would create ({label})")
                continue
            try:
                await _make_add_on(session, body, placeholder=body.name in placeholder_names)
                await session.commit()
                report.made += 1
                report.add(f"add-on  {body.name}: created ({label})")
            except (APIError, ValueError) as exc:
                await session.rollback()
                report.failed += 1
                report.add(f"add-on  {body.name}: FAILED ({getattr(exc, 'detail', exc)})")
    return report


def console(line: str) -> None:
    """The operator's console: this is a command, and its answer is the output."""
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


async def _main(path: Path | None, apply: bool) -> int:
    from callobuzz_rag.accounts.settings_store import read_engine_settings_row
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    data = load_seed(path)
    engine = create_engine(get_settings())
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            notice_days = (await read_engine_settings_row(session))[
                0
            ].trust.price_change_notice_days
        report = await run(factory, data, apply=apply, notice_days=notice_days)
    finally:
        await engine.dispose()
    for line in report.lines:
        console(line)
    verb = "created" if apply else "would be created"
    console(f"\n{report.made} {verb}, {report.left} left as they are, {report.failed} failed.")
    if not apply and report.made:
        console("This was a dry run. Run again with --apply to make them.")
    if report.left:
        console(
            "Items that already exist are never changed: set their prices in the plan editor "
            "and the add-on catalogue."
        )
    return EXIT_FAILED if report.failed else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed placeholder plans and add-ons for a hosted service."
    )
    parser.add_argument("--apply", action="store_true", help="make them (default: a dry run)")
    parser.add_argument(
        "--file", type=Path, default=None, help="a seed file instead of the bundled one"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(_main(args.file, args.apply))
    except Exception as exc:
        logger.error("Seeding failed: %s", exc)
        console(f"Seeding failed: {exc}")
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
