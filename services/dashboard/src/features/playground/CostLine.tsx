import { formatCostBoth } from "@/lib/format/cost";
import { formatMoney, useCurrency } from "@/lib/format/currency";
import { costStrings as c } from "@/lib/strings/costs";
import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import type { AnswerCost } from "./cost";

/**
 * The cost part of an answer's meta line: "1,200 tokens · ₹0.07 ($0.0008)". The figure is what the
 * request really cost (the provider's own number, helper calls included), in the chosen currency and
 * the other beside it. With no price it says "price unknown", never ₹0.00; an answer from an older
 * engine keeps the catalog estimate, marked "≈".
 */
export function CostLine({ tokens, cost }: { tokens: string; cost: AnswerCost }) {
	const currency = useCurrency();
	if (cost.estimated && cost.usd != null) {
		return (
			<span title={t.meta.estimated}>{fill(t.meta.tokens, { n: tokens, cost: formatMoney(cost.usd, currency) })}</span>
		);
	}
	const text = cost.known ? formatCostBoth(cost.usd, true, currency) : c.unknown;
	return (
		<span title={cost.known ? c.line.actualHint : c.line.unknownHint} data-testid="answer-cost">
			{fill(c.line.tokensCost, { n: tokens, cost: text })}
		</span>
	);
}
