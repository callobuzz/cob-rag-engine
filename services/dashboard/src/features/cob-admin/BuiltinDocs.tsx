import type { UseQueryResult } from "@tanstack/react-query";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Panel } from "@/features/kbs/ui";
import { formatRelative } from "@/lib/format/date";
import { cobAdminStrings as t } from "@/lib/strings/cob-admin";
import type { CobDocument } from "./api";
import { audienceLabel, docName } from "./docs-ui";

type Docs = { items: CobDocument[] };

/** The engine's own pages, read-only. Version, last sync, eval and Resync are the status card above. */
export function BuiltinDocs({ query }: { query: UseQueryResult<Docs> }) {
	const rows = (query.data?.items ?? []).filter((d) => d.source === "builtin");
	const columns: Column<CobDocument>[] = [
		{ id: "name", header: t.builtin.name, rowHeader: true, cell: (d) => docName(d), sortValue: (d) => d.name },
		{
			id: "audience",
			header: t.builtin.audience,
			cell: (d) => audienceLabel(d.audience),
			sortValue: (d) => d.audience,
		},
		{
			id: "added",
			header: t.builtin.added,
			cell: (d) => formatRelative(d.ingested_at),
			sortValue: (d) => d.ingested_at,
			align: "right",
		},
	];
	return (
		<Panel title={t.tabs.builtin} sub={t.builtin.note}>
			{query.isPending ? (
				<TableSkeleton rows={5} />
			) : query.isError ? (
				<div className="p-3">
					<ErrorState error={query.error} title={t.platform.loadError} onRetry={() => void query.refetch()} />
				</div>
			) : rows.length === 0 ? (
				<EmptyState title={t.builtin.empty} />
			) : (
				<div className="overflow-x-auto">
					<DataTable caption={t.builtin.caption} columns={columns} rows={rows} rowKey={(d) => d.id} />
				</div>
			)}
		</Panel>
	);
}
