import type { OwnBillingView } from "@/lib/api/billing-types";
import { formatMinor } from "@/lib/format/money";
import { formatNumber } from "@/lib/format/number";
import { planBillingStrings } from "@/lib/strings/billing-statements";
import { fill } from "@/lib/strings/en";
import { Panel } from "../kbs/ui";

const s = planBillingStrings.usage;

/**
 * "Usage this month" (BIL6): the questions asked against what the plan and its add-ons allow, and
 * what they have cost so far. A member sees the questions only; the amounts follow the plan's
 * "members see money" setting.
 */
export function UsageThisPeriod({ view, questionLimit }: { view: OwnBillingView; questionLimit: number | null }) {
	const usage = view.usage_this_period;
	if (!usage) return null;
	return (
		<Panel title={s.title} sub={s.lead}>
			<dl data-testid="usage-this-period" className="m-0 grid gap-0 px-4 py-1">
				<div className="flex justify-between gap-3 border-b border-dashed border-border py-2.5 text-[13.5px] last:border-b-0">
					<dt>{s.questions}</dt>
					<dd className="m-0 font-mono font-medium tabular-nums" data-testid="usage-questions">
						{questionLimit == null
							? fill(s.questionsOnly, { used: formatNumber(usage.questions) })
							: fill(s.questionsOf, { used: formatNumber(usage.questions), limit: formatNumber(questionLimit) })}
					</dd>
				</div>
				{usage.charged_minor != null ? (
					<div className="flex justify-between gap-3 py-2.5 text-[13.5px]">
						<dt className="grid">
							{s.charged}
							<small className="text-[12px] font-normal text-muted-foreground">{s.chargedHint}</small>
						</dt>
						<dd className="m-0 font-mono font-medium tabular-nums" data-testid="usage-charged">
							{formatMinor(usage.charged_minor, view.currency)}
						</dd>
					</div>
				) : null}
			</dl>
			<p className="m-0 px-4 pb-3 text-[12.5px] text-muted-foreground">
				{questionLimit == null ? s.allowanceUnlimited : fill(s.allowance, { limit: formatNumber(questionLimit) })}
			</p>
			{usage.unpriced ? (
				<p
					data-testid="usage-unpriced"
					className="m-0 border-t border-border px-4 py-2.5 text-[12.5px] text-muted-foreground"
				>
					{usage.unpriced === 1 ? s.unpricedOne : fill(s.unpriced, { n: formatNumber(usage.unpriced) })}
				</p>
			) : null}
		</Panel>
	);
}
