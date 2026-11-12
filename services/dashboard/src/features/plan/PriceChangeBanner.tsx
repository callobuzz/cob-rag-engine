import { Megaphone } from "lucide-react";
import { formatWhole } from "@/features/value/format";
import type { OwnBillingView } from "@/lib/api/billing-types";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { noSurprisesStrings } from "@/lib/strings/no-surprises";
import { formatDay } from "./dates";

const s = noSurprisesStrings.changes;

/**
 * The price changes the provider announced for this workspace and that have not started for it
 * yet (TV3): each with its date and what moves, old price to new. Nothing shows without one.
 */
export function PriceChangeBanner({ view }: { view: OwnBillingView }) {
	const { productName } = useBranding();
	const items = view.price_changes ?? [];
	if (items.length === 0) return null;
	return (
		<section
			aria-label={s.title}
			data-testid={s.testid}
			className="grid gap-2 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3"
		>
			<h2 className="flex items-center gap-2 text-[14.5px] font-bold">
				<Megaphone aria-hidden className="size-4" />
				{s.title}
			</h2>
			<p className="m-0 text-[13px] text-foreground-2">{fill(s.lead, { product: productName })}</p>
			<ul className="m-0 grid list-none gap-2 p-0">
				{items.map((c) => (
					<li key={`${c.plan_name}-${c.effective_on}`} className="grid gap-0.5 text-[13.5px]">
						<strong>{fill(s.item, { plan: c.plan_name, date: formatDay(c.effective_on) })}</strong>
						<ul className="m-0 grid list-none gap-0.5 p-0 text-[13px]">
							{c.changes.map((line) => (
								<li key={line.label}>
									{fill(s.row, {
										label: line.label,
										old: formatWhole(line.old_minor, view.currency),
										new: formatWhole(line.new_minor, view.currency),
									})}
								</li>
							))}
						</ul>
					</li>
				))}
			</ul>
		</section>
	);
}
