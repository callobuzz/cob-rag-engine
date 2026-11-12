import { fill } from "@/lib/strings/en";
import { valueStrings as t } from "@/lib/strings/value";
import type { ValueReport } from "./api";
import { formatMinutes, formatWhole } from "./format";

/**
 * The three lines the estimate is made of, in words, from the numbers the engine sent: nothing
 * here adds or multiplies money, it only prints the engine's figures. "Estimated, based on your
 * settings" always sits next to it.
 */
export function ValueFormula({ report }: { report: ValueReport }) {
	const cur = report.currency;
	const money = (minor: number) => formatWhole(minor, cur);
	const line = (key: "leads" | "handoffs" | "time") => report.breakdown.formula.find((l) => l.key === key);
	const leads = line("leads");
	const handoffs = line("handoffs");
	const time = line("time");
	const rows: { key: string; text: string }[] = [];
	if (leads) {
		const count = leads.inputs.count ?? 0;
		// Leads that no rule rated (the assistant has no qualification rules) are plain leads.
		const rated = report.inputs.leads_unrated === 0;
		const one = rated ? t.formula.qualifiedLead : t.formula.lead;
		const many = rated ? t.formula.qualifiedLeadsPlural : t.formula.leadsPlural;
		rows.push({
			key: "leads",
			text: fill(t.formula.leads, {
				count,
				leads: count === 1 ? one : many,
				value: money(leads.inputs.value_minor ?? 0),
				amount: money(leads.amount_minor),
			}),
		});
	}
	if (handoffs) {
		const count = handoffs.inputs.count ?? 0;
		rows.push({
			key: "handoffs",
			text: fill(t.formula.handoffs, {
				count,
				requests: count === 1 ? t.formula.request : t.formula.requestsPlural,
				value: money(handoffs.inputs.value_minor ?? 0),
				amount: money(handoffs.amount_minor),
			}),
		});
	}
	if (time) {
		const answered = report.inputs.answered;
		rows.push({
			key: "time",
			text: fill(t.formula.time, {
				answered,
				questions: answered === 1 ? t.formula.question : t.formula.questionsPlural,
				minutes: report.settings_used.minutes_per_answer,
				duration: formatMinutes(time.inputs.minutes_saved ?? 0),
				rate: money(time.inputs.staff_hour_minor ?? 0),
				amount: money(time.amount_minor),
			}),
		});
	}
	return (
		<ul data-testid="value-formula" className="m-0 grid list-none gap-2 p-0 text-[13.5px]">
			{rows.map((r) => (
				<li key={r.key} data-testid={`formula-${r.key}`} className="rounded-md bg-secondary px-3 py-2">
					{r.text}
				</li>
			))}
		</ul>
	);
}
