import { useQuery } from "@tanstack/react-query";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { formatNumber } from "@/lib/format/number";
import { modelsStrings } from "@/lib/strings/admin";
import { type EmbeddingEntry, embeddingCatalogQuery } from "./api";

const t = modelsStrings.embeddings;

/** `GET /models/embeddings`: the models a knowledge base can embed with. */
export function EmbeddingCatalog() {
	const q = useQuery(embeddingCatalogQuery);
	if (q.isPending) return <TableSkeleton rows={4} />;
	if (q.isError)
		return (
			<div className="p-4">
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			</div>
		);
	const columns: Column<EmbeddingEntry>[] = [
		{
			id: "name",
			header: t.cols.name,
			rowHeader: true,
			cell: (e) => <b className="font-semibold">{e.name ?? e.id}</b>,
		},
		{ id: "provider", header: t.cols.provider, stackLabel: true, cell: (e) => e.provider ?? "—" },
		{
			id: "model",
			header: t.cols.model,
			stackLabel: true,
			cell: (e) => <code className="font-mono text-[12px] break-all">{e.model ?? "—"}</code>,
		},
		{
			id: "dims",
			header: t.cols.dims,
			align: "right",
			stackLabel: true,
			cell: (e) => formatNumber(e.dimensions ?? null),
		},
		{ id: "notes", header: t.cols.notes, stackLabel: true, cell: (e) => e.notes ?? "" },
	];
	return (
		<>
			<p className="m-0 px-4 pt-3.5 text-[13px] text-foreground-2">{t.lead}</p>
			<DataTable
				caption={t.caption}
				columns={columns}
				rows={q.data}
				rowKey={(e) => e.id ?? `${e.provider}-${e.model}`}
			/>
		</>
	);
}
