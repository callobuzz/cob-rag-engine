import { CircleCheck, Clock } from "lucide-react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { Badge } from "@/components/ui/badge";
import { formatDateTime, formatRelative } from "@/lib/format/date";
import { leadsStrings } from "@/lib/strings/leads";
import type { Handoff, HandoffStatus } from "./handoffs-api";

const t = leadsStrings.handoffs;

/** Status as icon + text, never colour alone. */
export function HandoffStatusBadge({ status }: { status: HandoffStatus }) {
	return status === "new" ? (
		<Badge variant="info">
			<Clock aria-hidden />
			{t.statusNew}
		</Badge>
	) : (
		<Badge variant="ok">
			<CircleCheck aria-hidden />
			{t.statusHandled}
		</Badge>
	);
}

export function HandoffsTable({ rows, onOpen }: { rows: Handoff[]; onOpen: (id: string) => void }) {
	const columns: Column<Handoff>[] = [
		{
			id: "when",
			header: t.columns.when,
			className: "whitespace-nowrap",
			cell: (h) => (
				<time dateTime={h.created_at} title={formatDateTime(h.created_at)}>
					{formatRelative(h.created_at)}
				</time>
			),
		},
		// TV2 reference
		{
			id: "ref",
			header: leadsStrings.reference.label,
			className: "whitespace-nowrap font-mono text-[12.5px]",
			cell: (h) => h.ref_code ?? leadsStrings.reference.none,
		},
		// /TV2 reference
		{
			id: "channel",
			header: t.columns.channel,
			rowHeader: true,
			cell: (h) => (
				<button
					type="button"
					className="text-left font-semibold text-brand-text underline-offset-4 hover:underline"
					onClick={() => onOpen(h.id)}
				>
					{t.channels[h.channel] ?? h.channel}
				</button>
			),
		},
		{ id: "status", header: t.columns.status, cell: (h) => <HandoffStatusBadge status={h.status} /> },
		{ id: "why", header: t.columns.why, stackLabel: true, cell: (h) => t.reasons[h.reason] ?? h.reason_label },
		{
			id: "language",
			header: t.columns.language,
			stackLabel: true,
			cell: (h) => (h.language ? (leadsStrings.languages[h.language] ?? h.language) : "—"),
		},
		{
			id: "page",
			header: t.columns.page,
			stackLabel: true,
			className: "max-w-[16rem] truncate",
			cell: (h) => h.page_url ?? "—",
		},
	];
	return (
		<DataTable
			columns={columns}
			rows={rows}
			rowKey={(h) => h.id}
			caption={t.tableLabel}
			onRowClick={(h) => onOpen(h.id)}
		/>
	);
}
