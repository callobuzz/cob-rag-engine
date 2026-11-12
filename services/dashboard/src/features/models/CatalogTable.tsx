import { Star } from "lucide-react";
import { CopyButton } from "@/components/patterns/CopyButton";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { Badge } from "@/components/ui/badge";
import { formatMoney, useCurrency } from "@/lib/format/currency";
import { formatNumber } from "@/lib/format/number";
import { modelsStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import type { ModelEntry } from "./api";

const t = modelsStrings.catalog;

export function categoryLabel(c: string): string {
	return t.categories[c] ?? c;
}

/**
 * Answer models: name (recommended star), provider, id (mono, copy), price band, list prices per
 * 1M tokens in the display currency (₹ / $, an estimate), context window and streaming.
 */
export function CatalogTable({ models, caption, empty }: { models: ModelEntry[]; caption: string; empty?: string }) {
	const currency = useCurrency();
	const columns: Column<ModelEntry>[] = [
		{
			id: "name",
			header: t.cols.name,
			rowHeader: true,
			sortValue: (m) => m.name,
			cell: (m) => (
				<span className="inline-flex flex-wrap items-center gap-1.5 font-semibold">
					{m.name}
					{m.recommended ? (
						<Badge variant="info">
							<Star aria-hidden />
							{t.recommended}
						</Badge>
					) : null}
				</span>
			),
		},
		{
			id: "provider",
			header: t.cols.provider,
			stackLabel: true,
			sortValue: (m) => m.provider,
			cell: (m) => m.provider,
		},
		{
			id: "id",
			header: t.cols.id,
			stackLabel: true,
			cell: (m) => (
				<span className="inline-flex max-w-full flex-wrap items-center gap-1.5">
					<code className="font-mono text-[12px] break-all">{m.id}</code>
					<CopyButton value={m.id} ariaLabel={fill(t.copyId, { id: m.id })} />
				</span>
			),
		},
		{
			id: "category",
			header: t.cols.category,
			stackLabel: true,
			cell: (m) => <Badge>{categoryLabel(m.category)}</Badge>,
		},
		{
			id: "input",
			header: t.cols.input,
			align: "right",
			stackLabel: true,
			sortValue: (m) => m.input_cost_per_1m,
			cell: (m) => formatMoney(m.input_cost_per_1m, currency),
		},
		{
			id: "output",
			header: t.cols.output,
			align: "right",
			stackLabel: true,
			sortValue: (m) => m.output_cost_per_1m,
			cell: (m) => formatMoney(m.output_cost_per_1m, currency),
		},
		{
			id: "context",
			header: t.cols.context,
			align: "right",
			stackLabel: true,
			sortValue: (m) => m.context_window,
			cell: (m) => formatNumber(m.context_window),
		},
		{
			id: "streaming",
			header: t.cols.streaming,
			stackLabel: true,
			cell: (m) => (m.supports_streaming ? t.yes : t.no),
		},
	];
	return <DataTable caption={caption} columns={columns} rows={models} rowKey={(m) => m.id} empty={empty ?? t.empty} />;
}
