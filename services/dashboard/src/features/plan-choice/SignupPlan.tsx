import type { PublicPlan } from "@/lib/api/billing-types";
import { formatMinor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { planChoiceStrings as s } from "@/lib/strings/plan-choice";
import { findPlan, usePublicPlans } from "./api";

/**
 * The plan a visitor chose on the pricing page (`/signup?plan=<slug or id>`), resolved against the
 * engine's public list. Null while loading, for no `?plan=`, and for a name the engine does not
 * offer: the sign-up then starts on the default plan, as the engine would.
 */
export function useChosenPlan(ref: string | undefined): PublicPlan | null {
	const q = usePublicPlans(Boolean(ref));
	return findPlan(q.data?.items ?? [], ref);
}

/** "₹4,999.00 a month · ₹5,898.82 incl. GST", or "Free to start". */
export function signupPriceLine(plan: PublicPlan): string {
	const price = plan.price;
	if (plan.free || !price || price.subscription_fee_minor <= 0) return s.signup.freeLine;
	const vars = {
		price: formatMinor(price.subscription_fee_minor, price.currency),
		per: s.perPeriod[price.billing_period],
		total: formatMinor(price.inclusive_total_minor, price.currency),
	};
	return price.inclusive_total_minor === price.subscription_fee_minor
		? fill(s.signup.paidLineFlat, vars)
		: fill(s.signup.paidLine, vars);
}

/** The chosen plan on the sign-up form, with a plain note that nothing is charged yet. */
export function ChosenPlan({ plan }: { plan: PublicPlan }) {
	return (
		<section
			aria-label={s.signup.chosen}
			data-testid="signup-plan"
			className="grid gap-1 rounded-lg border border-border bg-secondary px-3.5 py-3"
		>
			<div className="flex flex-wrap items-baseline justify-between gap-2">
				<span className="text-[12.5px] text-muted-foreground">{s.signup.chosen}</span>
				<strong className="text-[15px]">{plan.name}</strong>
			</div>
			<p data-testid="signup-plan-price" className="m-0 text-[13px] text-foreground-2">
				{signupPriceLine(plan)}
			</p>
			{plan.free ? null : <p className="m-0 text-[12.5px] text-muted-foreground">{s.signup.paidNote}</p>}
		</section>
	);
}
