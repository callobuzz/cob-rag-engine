import { CalendarClock } from "lucide-react";
import { formatWhole } from "@/features/value/format";
import type { OwnBillingView } from "@/lib/api/billing-types";
import { fill } from "@/lib/strings/en";
import { noSurprisesStrings } from "@/lib/strings/no-surprises";
import { Panel } from "../kbs/ui";
import { formatDay } from "./dates";
import { ProjectionLine } from "./ProjectionLine";

const s = noSurprisesStrings.renewal;

/**
 * "Next renewal" (TV3): the day, the exact amount of the price in force that day, what is added
 * for the new period, and where this month is heading. Shown for a workspace whose plan charges
 * a subscription and only to people who may see money.
 */
export function RenewalCard({ view }: { view: OwnBillingView }) {
	const renewal = view.renewal;
	if (!renewal && view.projected_month_end_minor == null) return null;
	return (
		<Panel title={s.title}>
			<div data-testid="renewal-card" className="grid gap-2 p-4">
				{renewal ? (
					<>
						<p data-testid="renewal-line" className="m-0 flex items-start gap-2 text-[15px] font-semibold">
							<CalendarClock aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
							<span>
								{fill(s.on, {
									date: formatDay(renewal.on),
									amount: formatWhole(renewal.amount_minor, view.currency),
								})}
							</span>
						</p>
						{renewal.included_credit_minor > 0 ? (
							<p className="m-0 text-[13px] text-foreground-2">
								{fill(s.includes, { credit: formatWhole(renewal.included_credit_minor, view.currency) })}
							</p>
						) : null}
						{renewal.new_price_from ? (
							<p data-testid="renewal-new-price" className="m-0 text-[13px] font-medium text-foreground">
								{s.newPrice}
							</p>
						) : null}
					</>
				) : null}
				<ProjectionLine view={view} />
			</div>
		</Panel>
	);
}
