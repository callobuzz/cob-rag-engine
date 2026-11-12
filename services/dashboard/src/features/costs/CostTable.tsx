import { type Column, DataTable } from "@/components/patterns/DataTable";
import { formatCost } from "@/lib/format/cost";
import type { Currency } from "@/lib/format/currency";
import { formatNumber, formatPercent } from "@/lib/format/number";
import { costStrings as t } from "@/lib/strings/costs";
import { fill } from "@/lib/strings/en";
import type { CostRow } from "./api";

export type LabelledCostRow = CostRow & { name: string };

/**
 * One breakdown: name, questions, tokens, real cost (a request with no price reads "≥ …" or
 * "price unknown", never a flat zero) and the share of the total cost, with a totals row.
 */
export function CostTable({
	rows,
	totals,
	groupLabel,
	currency,
}: {
	rows: LabelledCostRow[];
	totals: CostRow;
	groupLabel: string;
	currency: Currency;
}) {
	const all = totals.cost_usd;
	const columns: Column<LabelledCostRow>[] = [
		{
			id: "name",
			header: t.table.name,
			rowHeader: true,
			sortValue: (r) => r.name,
			cell: (r) => <span className="font-semibold break-words">{r.name}</span>,
		},
		{
			id: "requests",
			header: t.table.requests,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.requests,
			cell: (r) => formatNumber(r.requests),
		},
		{
			id: "tokens",
			header: t.table.tokens,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.prompt_tokens + r.completion_tokens,
			cell: (r) => formatNumber(r.prompt_tokens + r.completion_tokens),
		},
		{
			id: "cost",
			header: t.table.cost,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.cost_usd,
			cell: (r) => (
				<span title={r.cost_known ? undefined : fill(t.page.unpricedNote, { n: formatNumber(r.unpriced_requests) })}>
					{formatCost(r.cost_usd, r.cost_known, currency, { exact: true })}
				</span>
			),
		},
		{
			id: "share",
			header: t.table.share,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.cost_usd,
			cell: (r) => (all > 0 ? formatPercent(r.cost_usd / all, r.cost_usd / all < 0.01 ? 1 : 0) : "—"),
		},
	];
	return (
		<DataTable
			columns={columns}
			rows={rows}
			rowKey={(r) => r.key ?? "none"}
			caption={fill(t.table.caption, { group: groupLabel })}
			initialSort={{ id: "cost", dir: "desc" }}
			footer={{
				name: t.table.total,
				requests: formatNumber(totals.requests),
				tokens: formatNumber(totals.prompt_tokens + totals.completion_tokens),
				cost: formatCost(totals.cost_usd, totals.cost_known, currency, { exact: true }),
				share: all > 0 ? formatPercent(1) : "—",
			}}
		/>
	);
}
