import { formatWhole } from "@/features/value/format";
import type { OwnBillingView } from "@/lib/api/billing-types";
import { fill } from "@/lib/strings/en";
import { noSurprisesStrings } from "@/lib/strings/no-surprises";

const s = noSurprisesStrings.projection;

/** "About ₹3,000 this month at this pace": a guide from usage so far, with how it is worked out. */
export function ProjectionLine({ view }: { view: OwnBillingView }) {
	if (view.projected_month_end_minor == null) return null;
	return (
		<div className="grid gap-0.5">
			<p data-testid="projection-line" className="m-0 text-[14px]">
				{fill(s.line, { amount: formatWhole(view.projected_month_end_minor, view.currency) })}
			</p>
			<small className="text-[12.5px] text-muted-foreground">{s.note}</small>
		</div>
	);
}
