import { CircleCheck, CircleDashed, CircleDot, CircleX, Clock, MailOpen, Trash2 } from "lucide-react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { Badge } from "@/components/ui/badge";
import { formatDateTime } from "@/lib/format/date";
import { leadsStrings as t } from "@/lib/strings/leads";
import type { LeadQualification, LeadStatus, LeadSummary } from "./api";
import { formatPhone } from "./format";

const STATUS: Record<LeadStatus, { variant: "ok" | "warn" | "bad" | "info" | "default"; icon: typeof Clock }> = {
	new: { variant: "info", icon: Clock },
	contacted: { variant: "warn", icon: MailOpen },
	won: { variant: "ok", icon: CircleCheck },
	lost: { variant: "default", icon: CircleX },
	spam: { variant: "bad", icon: Trash2 },
};

/** Status as icon + text, never colour alone. */
export function LeadStatusBadge({ status }: { status: LeadStatus }) {
	const m = STATUS[status];
	const Icon = m.icon;
	return (
		<Badge variant={m.variant}>
			<Icon aria-hidden />
			{t.status[status]}
		</Badge>
	);
}

const QUALIFICATION: Record<LeadQualification, { variant: "ok" | "warn" | "default"; icon: typeof Clock }> = {
	qualified: { variant: "ok", icon: CircleCheck },
	partial: { variant: "warn", icon: CircleDot },
	unqualified: { variant: "default", icon: CircleDashed },
};

/** The owner's bucket as icon + text, never colour alone; nothing when the lead has none. */
export function QualificationBadge({ value }: { value: LeadQualification | null | undefined }) {
	if (!value) return null;
	const m = QUALIFICATION[value];
	const Icon = m.icon;
	return (
		<Badge variant={m.variant}>
			<Icon aria-hidden />
			{t.qualification[value]}
		</Badge>
	);
}

export function LeadsTable({ rows, onOpen }: { rows: LeadSummary[]; onOpen: (id: string) => void }) {
	const columns: Column<LeadSummary>[] = [
		{
			id: "when",
			header: t.columns.when,
			cell: (l) => formatDateTime(l.created_at),
			className: "whitespace-nowrap",
		},
		{
			id: "name",
			header: t.columns.name,
			rowHeader: true,
			cell: (l) => (
				<button
					type="button"
					className="text-left font-semibold text-brand-text underline-offset-4 hover:underline"
					onClick={() => onOpen(l.id)}
				>
					{l.contact.name || t.noName}
				</button>
			),
		},
		{
			id: "phone",
			header: t.columns.phone,
			stackLabel: true,
			cell: (l) =>
				l.contact.phone ? (
					<a href={`tel:${l.contact.phone}`} className="whitespace-nowrap underline-offset-4 hover:underline">
						{formatPhone(l.contact.phone)}
					</a>
				) : (
					(l.contact.email ?? t.noPhone)
				),
		},
		{ id: "status", header: t.columns.status, cell: (l) => <LeadStatusBadge status={l.status} /> },
		{
			id: "qualification",
			header: t.columns.qualification,
			cell: (l) => <QualificationBadge value={l.qualification} />,
		},
		{ id: "why", header: t.columns.why, stackLabel: true, cell: (l) => t.triggers[l.trigger] ?? l.trigger },
		{ id: "kb", header: t.columns.kb, stackLabel: true, cell: (l) => l.knowledge_base },
		{
			id: "language",
			header: t.columns.language,
			stackLabel: true,
			cell: (l) => (l.language ? (t.languages[l.language] ?? l.language) : "—"),
		},
	];
	return (
		<DataTable
			columns={columns}
			rows={rows}
			rowKey={(l) => l.id}
			caption={t.listLabel}
			onRowClick={(l) => onOpen(l.id)}
		/>
	);
}
