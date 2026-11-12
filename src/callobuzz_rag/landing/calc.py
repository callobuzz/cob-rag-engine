"""The pricing page's calculator (LP3): what a number of customer questions costs a month.

Pure and integer-only: every amount is a whole number of minor units (paise, cents), there is no
float anywhere, and GST is the same half-up rule the price list uses. The page's script
(`landing/src/pages/calc.ts`) runs the same rule on the same cases
(`tests/fixtures/landing/calc_cases.json`), so what the server recommends and what the slider
shows cannot drift apart. The result is always an estimate: the page says so.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from callobuzz_rag.billing.invoicing.display import inclusive_total

UsageMode = Literal["included", "per_question", "cost_plus"]


@dataclass(frozen=True)
class CalcPlan:
    """One plan as the calculator sees it. A yearly plan is given by its monthly share."""

    key: str  # card index + variant, e.g. "1:INR:monthly"
    fee_minor: int  # per month (yearly: fee // 12)
    included_credit_minor: int  # per month (yearly: // 12)
    usage_mode: UsageMode
    per_question_minor: int  # per_question
    question_cap: int | None  # the plan's questions each month, None = no cap
    overage_charge_minor: int | None  # per question beyond the cap, None = not allowed beyond it
    est_answer_minor: int | None  # cost_plus: what one answer costs on the standard model


@dataclass(frozen=True)
class CalcResult:
    key: str
    fits: bool
    monthly_minor: int  # fee + usage beyond the included credit (+ overage), before GST
    monthly_incl_minor: int  # with GST when the seller is registered and prices exclude it


def monthly_cost(plan: CalcPlan, questions: int, *, gst: bool) -> CalcResult:
    """What `questions` a month cost on `plan`, and whether the plan allows that many."""
    fits = True
    usage = 0
    if plan.usage_mode == "per_question":
        usage = questions * plan.per_question_minor
    elif plan.usage_mode == "cost_plus":
        if plan.est_answer_minor is None:
            fits = False
        else:
            usage = questions * plan.est_answer_minor
    overage = 0
    if plan.question_cap is not None and questions > plan.question_cap:
        if plan.overage_charge_minor is None:
            fits = False
        else:
            overage = (questions - plan.question_cap) * plan.overage_charge_minor
    monthly = plan.fee_minor + max(0, usage - plan.included_credit_minor) + overage
    incl = inclusive_total(monthly, registered=gst, prices_include_gst=False)
    return CalcResult(plan.key, fits, monthly, incl)


def recommend(plans: Sequence[CalcPlan], questions: int, *, gst: bool) -> CalcResult | None:
    """The fitting plan with the lowest monthly cost; ties go to the lower fee, then the earlier
    card. None when no plan allows that many questions."""
    best: tuple[tuple[int, int, int], CalcResult] | None = None
    for index, plan in enumerate(plans):
        result = monthly_cost(plan, questions, gst=gst)
        if not result.fits:
            continue
        rank = (result.monthly_minor, plan.fee_minor, index)
        if best is None or rank < best[0]:
            best = (rank, result)
    return best[1] if best else None
