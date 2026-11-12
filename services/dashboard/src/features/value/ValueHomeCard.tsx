import { Link } from "@tanstack/react-router";
import { buttonVariants } from "@/components/ui/button";
import type { Home } from "@/features/home/api";
import { Panel } from "@/features/kbs/ui";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { valueStrings as t } from "@/lib/strings/value";
import { formatWhole } from "./format";

/**
 * The Home value card: this month so far, an estimate from the owner's own values, with a way into
 * the full report and a nudge to set the values when only the suggestions are in use. The card is
 * absent when the provider switched the value report off.
 */
export function ValueHomeCard({ value, owner }: { value: NonNullable<Home["value"]>; owner: boolean }) {
	const m = value.month_to_date;
	return (
		<Panel title={t.home.title} sub={t.home.sub}>
			<div className="grid gap-3 p-4" data-testid="home-value">
				<p data-testid="home-value-total" className="m-0 font-display text-3xl font-bold">
					{formatWhole(m.total_minor, m.currency)}
				</p>
				<dl className="m-0 grid grid-cols-3 gap-3 text-[12.5px]">
					<div className="flex flex-col-reverse justify-end gap-0.5">
						<dt className="text-muted-foreground">{t.home.answered}</dt>
						<dd className="m-0 text-base font-bold">{formatNumber(m.answered)}</dd>
					</div>
					<div className="flex flex-col-reverse justify-end gap-0.5">
						<dt className="text-muted-foreground">{t.home.leads}</dt>
						<dd className="m-0 text-base font-bold">{formatNumber(m.leads_qualified)}</dd>
					</div>
					<div className="flex flex-col-reverse justify-end gap-0.5">
						<dt className="text-muted-foreground">{t.home.handoffs}</dt>
						<dd className="m-0 text-base font-bold">{formatNumber(m.handoffs)}</dd>
					</div>
				</dl>
				{value.last_month_total_minor != null ? (
					<p className="m-0 text-[12.5px] text-muted-foreground">
						{fill(t.home.lastMonth, { amount: formatWhole(value.last_month_total_minor, m.currency) })}
					</p>
				) : null}
				{!value.saved_settings ? (
					<p data-testid="home-value-set" className="m-0 text-[12.5px] text-foreground-2">
						{t.home.setPrompt}{" "}
						{owner ? (
							<Link to="/value" search={{ edit: 1 }} className="font-semibold">
								{t.home.set}
							</Link>
						) : null}
					</p>
				) : null}
				<div>
					<Link to="/value" className={buttonVariants({ variant: "outline", size: "sm" })}>
						{t.home.see}
					</Link>
				</div>
			</div>
		</Panel>
	);
}
