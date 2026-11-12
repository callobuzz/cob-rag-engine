import { type Column, DataTable } from "@/components/patterns/DataTable";
import { formatCost } from "@/lib/format/cost";
import type { Currency } from "@/lib/format/currency";
import { formatNumber, formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { usageStrings as t } from "@/lib/strings/overview";
import type { CsvRow } from "./csv";

/** One grouped row with its readable label (day, knowledge base, source or customer). */
export type LabelledRow = CsvRow;

/**
 * The invoice table: label, questions, prompt and completion tokens, estimated cost in the display
 * currency (two decimals) and share of questions; sortable; a totals row from the response's
 * `totals` (so it always matches the summary strip and the CSV).
 */
export function UsageTable({
	rows,
	totals,
	groupLabel,
	currency,
	initialSort,
	showCost = true,
	costHeader = t.table.cost,
}: {
	rows: LabelledRow[];
	totals: LabelledRow;
	groupLabel: string;
	currency: Currency;
	initialSort: { id: string; dir: "asc" | "desc" };
	/** Hidden for a customer who has no AI spend (their answers run on the operator's key). */
	showCost?: boolean;
	costHeader?: string;
}) {
	const all = totals.requests;
	const columns: Column<LabelledRow>[] = [
		{
			id: "label",
			header: t.table.label,
			rowHeader: true,
			sortValue: (r) => r.key ?? "",
			cell: (r) => <span className="font-semibold break-words">{r.label}</span>,
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
			id: "prompt",
			header: t.table.prompt,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.prompt_tokens,
			cell: (r) => formatNumber(r.prompt_tokens),
		},
		{
			id: "completion",
			header: t.table.completion,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.completion_tokens,
			cell: (r) => formatNumber(r.completion_tokens),
		},
		...(showCost
			? [
					{
						id: "cost",
						header: costHeader,
						align: "right" as const,
						stackLabel: true,
						sortValue: (r: LabelledRow) => r.cost_usd,
						cell: (r: LabelledRow) => formatCost(r.cost_usd, r.cost_known, currency, { exact: true }),
					},
				]
			: []),
		{
			id: "share",
			header: t.table.share,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.requests,
			cell: (r) => {
				const share = all ? r.requests / all : 0;
				return (
					<span className="inline-flex items-center justify-end gap-2">
						<span
							aria-hidden
							className="inline-block h-1.5 w-12 overflow-hidden rounded-[3px] bg-secondary max-[600px]:hidden"
						>
							<i className="block h-full rounded-[3px] bg-chart-1" style={{ width: `${Math.round(share * 100)}%` }} />
						</span>
						{all ? formatPercent(share, share > 0 && share < 0.01 ? 1 : 0) : "—"}
					</span>
				);
			},
		},
	];
	return (
		<DataTable
			columns={columns}
			rows={rows}
			rowKey={(r) => r.key ?? "none"}
			caption={fill(t.table.caption, { group: groupLabel })}
			initialSort={initialSort}
			footer={{
				label: t.table.total,
				requests: formatNumber(totals.requests),
				prompt: formatNumber(totals.prompt_tokens),
				completion: formatNumber(totals.completion_tokens),
				cost: formatCost(totals.cost_usd, totals.cost_known, currency, { exact: true }),
				share: all ? formatPercent(1) : "—",
			}}
		/>
	);
}
