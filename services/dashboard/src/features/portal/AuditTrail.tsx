import { useQuery } from "@tanstack/react-query";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Panel } from "@/features/kbs/ui";
import { formatDateTime } from "@/lib/format/date";
import { portalStrings as t } from "@/lib/strings/portal";
import { AUDIT_PAGE_SIZE, type AuditEntry, auditQuery } from "./api";

const a = t.audit;

/** Old → new text as lines marked "−" (removed) and "+" (added): text markers, not colour alone. */
export function Diff({ oldText, newText }: { oldText?: string | null; newText?: string | null }) {
	if (!oldText && !newText) return <span className="text-muted-foreground">—</span>;
	return (
		<div className="grid gap-1 font-mono text-[12px]">
			{oldText ? (
				<p className="m-0 rounded-sm bg-destructive-soft px-2 py-1 break-words whitespace-pre-wrap">
					<span aria-hidden>− </span>
					<span className="sr-only">{a.removed}: </span>
					{oldText}
				</p>
			) : null}
			{newText ? (
				<p className="m-0 rounded-sm bg-success-soft px-2 py-1 break-words whitespace-pre-wrap">
					<span aria-hidden>+ </span>
					<span className="sr-only">{a.addedText}: </span>
					{newText}
				</p>
			) : null}
		</div>
	);
}

/** Chunk ids in a details value read as the chunk's number ("#3") when it is in this workspace. */
function detailValue(value: unknown, numberOf: (chunkId: string) => number | null): string {
	if (value === null || value === undefined || value === "") return "—";
	if (Array.isArray(value)) return value.map((v) => detailValue(v, numberOf)).join(", ");
	if (typeof value === "string") {
		const n = numberOf(value);
		return n ? `#${n}` : value;
	}
	if (typeof value === "object") {
		return Object.entries(value as Record<string, unknown>)
			.map(([k, v]) => `${k}: ${detailValue(v, numberOf)}`)
			.join(" · ");
	}
	return String(value);
}

/**
 * What an action recorded (`details`): an object as a short key/value list (split
 * {position, source_chunk_id}, merge {source_chunk_ids}, approve / reject {reason}, edit_metadata
 * {metadata}); a plain string on older entries as it is.
 */
export function AuditDetails({
	details,
	numberOf,
}: {
	details: AuditEntry["details"];
	numberOf: (chunkId: string) => number | null;
}) {
	if (details === null || details === undefined || details === "") return null;
	if (typeof details === "string") return <small className="block text-muted-foreground">{details}</small>;
	const entries = Object.entries(details);
	if (entries.length === 0) return null;
	return (
		<dl className="m-0 mt-0.5 grid gap-0.5 text-[12px] text-muted-foreground">
			{entries.map(([k, v]) => (
				<div key={k} className="flex flex-wrap gap-x-1">
					<dt className="font-medium">{a.detailKeys[k] ?? k}:</dt>
					<dd className="m-0 break-words">{detailValue(v, numberOf)}</dd>
				</div>
			))}
		</dl>
	);
}

/** Entries recorded without a time go last; the others keep the engine's newest-first order. */
export function timedFirst<T extends { timestamp?: string | null }>(entries: T[]): T[] {
	return [...entries.filter((e) => e.timestamp), ...entries.filter((e) => !e.timestamp)];
}

/** Audit trail (`GET …/audit-trail`): time, who, action, chunk and the text change, newest first. */
export function AuditTrail({
	workspaceId,
	page,
	onPage,
	numberOf,
}: {
	workspaceId: string;
	page: number;
	onPage: (p: number) => void;
	numberOf: (chunkId: string) => number | null;
}) {
	const q = useQuery(auditQuery(workspaceId, page));
	const columns: Column<AuditEntry & { key: string }>[] = [
		{
			id: "time",
			header: a.colTime,
			cell: (e) =>
				e.timestamp ? (
					<span className="whitespace-nowrap">{formatDateTime(e.timestamp)}</span>
				) : (
					<span title={a.noTimeLabel}>
						<span aria-hidden>{a.noTime}</span>
						<span className="sr-only">{a.noTimeLabel}</span>
					</span>
				),
		},
		{ id: "user", header: a.colUser, stackLabel: true, cell: (e) => <span className="break-all">{e.user}</span> },
		{
			id: "action",
			header: a.colAction,
			stackLabel: true,
			cell: (e) => (
				<span>
					{a.actions[e.action] ?? e.action}
					<AuditDetails details={e.details} numberOf={numberOf} />
				</span>
			),
		},
		{
			id: "chunk",
			header: a.colChunk,
			stackLabel: true,
			cell: (e) => {
				if (!e.chunk_id) return "—";
				const n = numberOf(e.chunk_id);
				return n ? `#${n}` : <span className="font-mono text-[12px]">{e.chunk_id.slice(0, 8)}</span>;
			},
		},
		{
			id: "change",
			header: a.colChange,
			stackLabel: true,
			className: "min-w-[240px]",
			cell: (e) => <Diff oldText={e.old_text} newText={e.new_text} />,
		},
	];
	const rows = timedFirst(q.data?.entries ?? []).map((e, i) => ({
		...e,
		key: `${e.timestamp ?? "none"}-${e.chunk_id ?? ""}-${i}`,
	}));
	return (
		<Panel title={a.title}>
			{q.isPending ? (
				<TableSkeleton rows={3} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} onRetry={() => void q.refetch()} />
				</div>
			) : rows.length === 0 ? (
				<p className="m-0 p-4 text-foreground-2">{a.empty}</p>
			) : (
				<div className="overflow-x-auto">
					<DataTable caption={a.caption} columns={columns} rows={rows} rowKey={(e) => e.key} />
					{(q.data?.total ?? 0) > AUDIT_PAGE_SIZE ? (
						<Pager page={page} pageSize={AUDIT_PAGE_SIZE} total={q.data?.total ?? 0} onPage={onPage} />
					) : null}
				</div>
			)}
		</Panel>
	);
}
