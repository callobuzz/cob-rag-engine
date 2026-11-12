import { PiggyBank } from "lucide-react";
import type { ReactNode } from "react";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import { useCurrency } from "@/lib/format/currency";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";
import {
	type FastModelBenchmarks,
	type FastModelResult,
	fastBudget,
	findResult,
	friendlyName,
	pct,
	perThousand,
	savingPercent,
} from "./api";

/** The budget option and what it saves against the model in use, or null when nothing is saved. */
export function budgetOffer(
	data: FastModelBenchmarks | null | undefined,
	current: { model: string | null | undefined; hosts?: readonly string[] },
): { budget: FastModelResult; baseline: FastModelResult; save: number } | null {
	const budget = fastBudget(data);
	if (!budget || !data) return null;
	const models = data.models;
	const baseline =
		findResult(models, current.model, current.hosts ?? []) ??
		models.find((m) => m.key === data.recommendations.default);
	if (!baseline || baseline.key === budget.key) return null;
	const save = savingPercent(baseline.usd_per_1000_labels, budget.usd_per_1000_labels);
	return save == null ? null : { budget, baseline, save };
}

/**
 * "Save 64%: switch to Gemma 4 26B". On a knowledge base the button saves the model through the
 * form's own save path (owner-only); on the engine page, which only reads its settings, `action`
 * is the line to copy instead.
 */
export function BudgetCallout({
	offer,
	onSwitch,
	action,
}: {
	offer: { budget: FastModelResult; baseline: FastModelResult; save: number };
	onSwitch?: (m: FastModelResult) => void;
	action?: ReactNode;
}) {
	const currency = useCurrency();
	const { budget, baseline, save } = offer;
	const name = friendlyName(budget.model);
	return (
		<section
			aria-label={t.budgetCallout.region}
			className="flex flex-wrap items-center gap-3 rounded-lg border border-[color-mix(in_srgb,var(--success)_35%,var(--border))] bg-success-soft p-3.5"
		>
			<PiggyBank className="size-6 shrink-0 text-success" aria-hidden />
			<div className="min-w-[200px] flex-1">
				<h3 className="m-0 text-[14px] font-bold">{fill(t.budgetCallout.title, { save, name })}</h3>
				<p className="m-0 text-[13px] text-foreground-2">
					{fill(t.budgetCallout.body, {
						cost: perThousand(budget.usd_per_1000_labels, currency),
						baseCost: perThousand(baseline.usd_per_1000_labels, currency),
						acc: pct(budget.overall),
					})}
				</p>
			</div>
			{action ??
				(onSwitch ? (
					<OwnerGate action="kb.changeModel">
						<Button type="button" size="sm" onClick={() => onSwitch(budget)}>
							{t.budgetCallout.switch}
						</Button>
					</OwnerGate>
				) : null)}
		</section>
	);
}
