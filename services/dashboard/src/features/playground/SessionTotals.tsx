import { formatCostBoth } from "@/lib/format/cost";
import { formatMoney, useCurrency } from "@/lib/format/currency";
import { formatNumber } from "@/lib/format/number";
import { costStrings as c } from "@/lib/strings/costs";
import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { answerCost, costUsd, type ModelEntry, tokensFrom } from "./cost";
import type { KbSession } from "./session";

/**
 * Questions answered in this session (a compare counts two), what they cost in USD (the engine's real
 * figure per answer; a catalog estimate only for an answer without one), how many had no price and
 * how many were estimated. An unpriced answer adds nothing to `usd` and is counted in `unknown`,
 * never counted as free.
 */
export function sessionTotals(s: KbSession, catalog: readonly ModelEntry[] | undefined) {
	let questions = 0;
	let usd = 0;
	let unknown = 0;
	let estimated = 0;
	const add = (model: string | null | undefined, tokens: ReturnType<typeof tokensFrom>) => {
		questions++;
		const cost = answerCost(tokens, costUsd(catalog, model, tokens));
		if (cost.usd == null || !cost.known) unknown++;
		if (cost.estimated) estimated++;
		usd += cost.usd ?? 0;
	};
	for (const m of s.messages) {
		if (m.role !== "bot" || m.status !== "done") continue;
		add(m.model, m.tokens);
	}
	for (const cmp of s.compares) {
		for (const side of [cmp.a, cmp.b]) {
			if (side.status !== "done" || !side.response) continue;
			add(side.response.model_used, tokensFrom(side.response.token_usage));
		}
	}
	return { questions, usd, unknown, estimated };
}

/** Header chip "This session: {n} questions · ₹x ($y)" (in memory; Clear resets). */
export function SessionTotals({
	session,
	catalog,
}: {
	session: KbSession;
	catalog: readonly ModelEntry[] | undefined;
}) {
	const currency = useCurrency();
	const { questions, usd, unknown, estimated } = sessionTotals(session, catalog);
	const label = t.page.sessionLabel;
	let text: string;
	if (estimated > 0 && unknown === 0) {
		// An older engine reported no cost: the catalog estimate, marked "≈" as before.
		const cost = formatMoney(usd, currency);
		text =
			questions === 1 ? fill(t.page.sessionOne, { cost }) : fill(t.page.session, { n: formatNumber(questions), cost });
	} else {
		const priced = questions - unknown;
		const cost =
			questions === 0
				? formatCostBoth(0, true, currency)
				: priced === 0
					? c.unknown
					: unknown > 0
						? fill(c.session.unknownSome, { cost: formatCostBoth(usd, true, currency), n: unknown })
						: formatCostBoth(usd, true, currency);
		text = questions === 1 ? fill(c.session.one, { cost }) : fill(c.session.many, { n: formatNumber(questions), cost });
	}
	return (
		<span
			data-testid="session-totals"
			title={label}
			className="inline-flex h-9 items-center rounded-sm border border-border bg-secondary px-3 text-[12.5px] font-semibold text-foreground-2"
		>
			{text}
		</span>
	);
}
