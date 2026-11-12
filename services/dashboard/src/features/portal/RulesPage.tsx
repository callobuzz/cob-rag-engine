import { CircleCheck, CirclePause, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import type { Corpus } from "@/lib/api/queries";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { portal, type Rule, useRuleMutation, useRules } from "./api";
import { toastPortalError } from "./messages";
import { RuleDialog } from "./RuleDialog";

/** A rule's condition in words ("Type is application/pdf", "larger than 10 MB"…). */
export function conditionText(rule: Pick<Rule, "rule_type" | "condition">): string {
	const c = (rule.condition ?? {}) as Record<string, unknown>;
	const n = (x: unknown) => (typeof x === "number" ? formatNumber(x) : String(x));
	switch (rule.rule_type) {
		case "mime_type": {
			const types = Array.isArray(c.mime_types) ? c.mime_types.map(String) : c.mime_type ? [String(c.mime_type)] : [];
			return fill(t.rules.conditionMime, { types: types.join(", ") || "—" });
		}
		case "size": {
			const parts: string[] = [];
			if (c.size_gt_mb != null) parts.push(fill(t.rules.conditionSizeGt, { n: n(c.size_gt_mb) }));
			if (c.size_lt_mb != null) parts.push(fill(t.rules.conditionSizeLt, { n: n(c.size_lt_mb) }));
			const s = parts.join(" and ");
			return s ? s.charAt(0).toUpperCase() + s.slice(1) : "—";
		}
		case "confidence": {
			const s = fill(t.rules.conditionConfidence, { n: String(c.confidence_below ?? "—") });
			return s.charAt(0).toUpperCase() + s.slice(1);
		}
		case "always":
			return t.rules.conditionAlways;
		default:
			return JSON.stringify(c);
	}
}

/** Routing rules of a knowledge base (`/portal/corpus/{kb}/rules`): list, create, edit, delete. */
export function RulesPage({ kb }: { kb: Corpus }) {
	const q = useRules(kb.id);
	const [editing, setEditing] = useState<Rule | null>(null);
	const [open, setOpen] = useState(false);
	const [deleting, setDeleting] = useState<Rule | null>(null);
	const del = useRuleMutation(kb.id, (id: string) => portal.deleteRule(kb.id, id));
	const rules = [...(q.data?.items ?? [])].sort((a, b) => a.priority - b.priority);
	const indexOf = (r: Rule) => rules.findIndex((x) => x.id === r.id) + 1;

	const columns: Column<Rule>[] = [
		{
			id: "type",
			header: t.rules.colType,
			rowHeader: true,
			cell: (r) => <b className="font-semibold">{t.rules.types[r.rule_type] ?? r.rule_type}</b>,
		},
		{
			id: "condition",
			header: t.rules.colCondition,
			stackLabel: true,
			cell: (r) => <span className="break-words">{conditionText(r)}</span>,
		},
		{ id: "action", header: t.rules.colAction, stackLabel: true, cell: (r) => t.rules.actions[r.action] ?? r.action },
		{
			id: "priority",
			header: t.rules.colPriority,
			align: "right",
			stackLabel: true,
			cell: (r) => <span className="tabular-nums">{formatNumber(r.priority)}</span>,
		},
		{
			id: "enabled",
			header: t.rules.colEnabled,
			stackLabel: true,
			cell: (r) =>
				r.enabled ? (
					<Badge variant="ok">
						<CircleCheck aria-hidden />
						{t.rules.enabled}
					</Badge>
				) : (
					<Badge>
						<CirclePause aria-hidden />
						{t.rules.disabled}
					</Badge>
				),
		},
		{
			id: "actions",
			header: t.rules.colActions,
			align: "right",
			cell: (r) => (
				<div className="flex justify-end gap-1.5">
					<Button
						size="sm"
						variant="outline"
						aria-label={fill(t.rules.editLabel, { n: indexOf(r) })}
						onClick={() => {
							setEditing(r);
							setOpen(true);
						}}
					>
						<Pencil aria-hidden />
						<span className="max-[520px]:sr-only">{t.rules.edit}</span>
					</Button>
					<Button
						size="sm"
						variant="outline"
						className="text-destructive"
						aria-label={fill(t.rules.deleteLabel, { n: indexOf(r) })}
						onClick={() => setDeleting(r)}
					>
						<Trash2 aria-hidden />
						<span className="max-[520px]:sr-only">{t.rules.delete}</span>
					</Button>
				</div>
			),
		},
	];

	return (
		<Panel
			title={t.rules.title}
			sub={fill(t.rules.sub, { kb: kb.name })}
			aside={
				<Button
					variant="default"
					onClick={() => {
						setEditing(null);
						setOpen(true);
					}}
				>
					<Plus aria-hidden />
					{t.rules.add}
				</Button>
			}
		>
			{q.isPending ? (
				<TableSkeleton rows={2} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} onRetry={() => void q.refetch()} />
				</div>
			) : rules.length === 0 ? (
				<p className="m-0 p-4 text-foreground-2" data-testid="rules-empty">
					{t.rules.empty}
				</p>
			) : (
				<div className="overflow-x-auto">
					<DataTable caption={t.rules.caption} columns={columns} rows={rules} rowKey={(r) => r.id} />
				</div>
			)}
			<RuleDialog open={open} onOpenChange={setOpen} kb={kb.id} rule={editing} />
			<ConfirmDialog
				open={!!deleting}
				onOpenChange={(o) => {
					if (!o) setDeleting(null);
				}}
				title={t.rules.deleteTitle}
				body={deleting ? `${t.rules.types[deleting.rule_type] ?? deleting.rule_type}: ${conditionText(deleting)}` : ""}
				confirmLabel={t.rules.delete}
				pending={del.isPending}
				onConfirm={() => {
					if (!deleting) return;
					del.mutate(deleting.id, {
						onSuccess: () => {
							toast.success(t.rules.deleted);
							setDeleting(null);
						},
						onError: (e) => toastPortalError(e),
					});
				}}
			>
				<p className="m-0 text-[13px] text-foreground-2">{t.rules.deleteBody}</p>
			</ConfirmDialog>
		</Panel>
	);
}
