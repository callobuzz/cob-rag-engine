import { useState } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format/date";
import { auditStrings as t } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { actionLabel } from "./actions";
import type { AuditEntry } from "./api";

/** One short line of a value: strings as they are, lists joined, objects as compact JSON. */
function short(v: unknown): string {
	if (v == null) return "—";
	if (typeof v === "string") return v;
	if (typeof v === "number" || typeof v === "boolean") return String(v);
	if (Array.isArray(v)) return v.map(short).join(", ");
	return JSON.stringify(v);
}

/** "changed: branding.product_name, signup_mode · reason: …" (the first three keys). */
export function detailsSummary(details: Record<string, unknown>): string {
	const keys = Object.keys(details);
	if (keys.length === 0) return "";
	const text = keys
		.slice(0, 3)
		.map((k) => `${k}: ${short(details[k])}`)
		.join(" · ");
	return keys.length > 3 ? `${text} · …` : text;
}

function Details({ row }: { row: AuditEntry }) {
	const [open, setOpen] = useState(false);
	const summary = detailsSummary(row.details);
	if (!summary) return <span className="text-muted-foreground">{t.none}</span>;
	return (
		<div className="grid max-w-[340px] min-w-0 gap-1">
			<span className="break-words text-foreground-2">{summary}</span>
			<Button
				size="sm"
				variant="ghost"
				className="w-fit px-1.5"
				aria-expanded={open}
				onClick={() => setOpen((o) => !o)}
			>
				{open ? t.hideDetails : t.showDetails}
			</Button>
			{open ? (
				<section
					aria-label={fill(t.detailsLabel, { action: actionLabel(row.action), time: formatDateTime(row.created_at) })}
					// biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-reachable (WCAG 2.1.1)
					tabIndex={0}
					data-testid="audit-details"
					className="max-h-60 overflow-auto rounded-sm border border-border bg-muted p-2 focus-visible:outline-2 focus-visible:outline-ring"
				>
					<pre className="m-0 font-mono text-[12px] break-words whitespace-pre-wrap">
						{JSON.stringify(row.details, null, 2)}
					</pre>
				</section>
			) : null}
		</div>
	);
}

export function AuditTable({ rows, empty }: { rows: AuditEntry[]; empty: string }) {
	const columns: Column<AuditEntry>[] = [
		{
			id: "time",
			header: t.cols.time,
			rowHeader: true,
			cell: (r) => <span className="whitespace-nowrap tabular-nums">{formatDateTime(r.created_at)}</span>,
		},
		{
			id: "actor",
			header: t.cols.actor,
			stackLabel: true,
			cell: (r) => (
				<span className="grid min-w-0 gap-1">
					<span className="break-all">{r.actor_label}</span>
					<Badge className="w-fit">{t.kinds[r.actor_kind] ?? r.actor_kind}</Badge>
				</span>
			),
		},
		{
			id: "action",
			header: t.cols.action,
			stackLabel: true,
			cell: (r) => (
				<span className="grid gap-0.5">
					<span className="font-semibold">{actionLabel(r.action)}</span>
					<code className="font-mono text-[11.5px] text-muted-foreground">{r.action}</code>
				</span>
			),
		},
		{
			id: "customer",
			header: t.cols.customer,
			stackLabel: true,
			cell: (r) => r.tenant_label ?? <span className="text-muted-foreground">{t.none}</span>,
		},
		{
			id: "target",
			header: t.cols.target,
			stackLabel: true,
			cell: (r) =>
				r.target_type || r.target_id ? (
					<span className="grid gap-0.5">
						{r.target_type ? <span>{r.target_type}</span> : null}
						{r.target_id ? (
							<code className="font-mono text-[11.5px] break-all text-muted-foreground">{r.target_id}</code>
						) : null}
					</span>
				) : (
					<span className="text-muted-foreground">{t.none}</span>
				),
		},
		{ id: "details", header: t.cols.details, stackLabel: true, cell: (r) => <Details row={r} /> },
		{
			id: "ip",
			header: t.cols.ip,
			stackLabel: true,
			cell: (r) => <span className="font-mono text-[12px]">{r.ip ?? t.none}</span>,
		},
	];
	return <DataTable caption={t.page.caption} columns={columns} rows={rows} rowKey={(r) => r.id} empty={empty} />;
}
