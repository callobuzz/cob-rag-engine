import { Link } from "@tanstack/react-router";
import { History } from "lucide-react";
import type { Column } from "@/components/patterns/DataTable";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { UsageMeter } from "@/components/patterns/UsageMeter";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { FairUseState } from "@/features/fair-use/api"; // PIP4
import { StateBadge } from "@/features/fair-use/labels"; // PIP4
import { KbAvatar } from "@/features/kbs/KbAvatar";
import type { AdminTenant } from "@/lib/api/accounts-types";
import { formatRelative, formatShortDate } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { operatorStrings, customersStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { customerLimitsStrings } from "@/lib/strings/pipelines"; // PIP4
import type { TenantStatusTab } from "./api";
import type { LifecycleAction } from "./LifecycleDialogs";

export function peopleText(n: number): string {
	return n === 1 ? t.list.person : fill(t.list.people, { count: formatNumber(n) });
}

export function kbsText(tenant: AdminTenant): string {
	return fill(t.list.kbsOf, {
		used: formatNumber(tenant.corpus_count),
		limit: tenant.effective_max_corpora == null ? "∞" : formatNumber(tenant.effective_max_corpora),
	});
}

/** `/admin/audit?tenant_id=…&action=tenant.` (D10 builds the page; its filters are URL params). */
export function historySearch(tenant: AdminTenant) {
	return { tenant_id: tenant.id, action: "tenant." };
}

/** "Your workspace": the operator's own, never a customer. */
export function OperatorBadge() {
	return (
		<Badge variant="info" data-testid="operator-badge">
			{operatorStrings.badge}
		</Badge>
	);
}

export function PlanBadge({ tenant }: { tenant: Pick<AdminTenant, "plan_name"> }) {
	return <Badge variant="info">{tenant.plan_name}</Badge>;
}

function Who({ tenant, sub }: { tenant: AdminTenant; sub: string }) {
	return (
		<div className="flex min-w-0 items-center gap-2.5">
			<KbAvatar kb={tenant} size={28} />
			<div className="min-w-0">
				<Link
					to="/admin/customers/$tenantId"
					params={{ tenantId: tenant.id }}
					className="block truncate font-semibold text-foreground no-underline hover:underline"
				>
					{tenant.name}
				</Link>
				<small className="block truncate text-[12px] text-muted-foreground">{sub}</small>
			</div>
		</div>
	);
}

function Act({
	label,
	tenant,
	variant,
	onClick,
}: {
	label: string;
	tenant: AdminTenant;
	variant?: "default" | "ghost" | "destructive";
	onClick: () => void;
}) {
	return (
		<Button
			size="sm"
			variant={variant}
			aria-label={fill(t.actions.forName, { action: label, name: tenant.name })}
			onClick={onClick}
		>
			{label}
		</Button>
	);
}

function Actions({
	tenant,
	status,
	onAction,
}: {
	tenant: AdminTenant;
	status: TenantStatusTab;
	onAction: (a: LifecycleAction, t: AdminTenant) => void;
}) {
	const a = t.actions;
	// The operator's own workspace is never suspended or declined (and it is not in these lists).
	if (tenant.is_operator) return null;
	return (
		<div className="flex flex-wrap justify-end gap-1.5 max-[600px]:justify-start min-[1101px]:flex-nowrap">
			{status === "pending" ? (
				<>
					<Act label={a.decline} tenant={tenant} variant="ghost" onClick={() => onAction("decline", tenant)} />
					<Act label={a.activate} tenant={tenant} variant="default" onClick={() => onAction("activate", tenant)} />
				</>
			) : status === "active" ? (
				<>
					<Act label={a.changePlan} tenant={tenant} onClick={() => onAction("changePlan", tenant)} />
					<Act label={a.suspend} tenant={tenant} onClick={() => onAction("suspend", tenant)} />
					<Act label={a.delete} tenant={tenant} variant="destructive" onClick={() => onAction("delete", tenant)} />
				</>
			) : (
				<>
					<Act label={a.reactivate} tenant={tenant} variant="default" onClick={() => onAction("reactivate", tenant)} />
					<Act label={a.delete} tenant={tenant} variant="destructive" onClick={() => onAction("delete", tenant)} />
				</>
			)}
		</div>
	);
}

/** Columns per tab (mockup `[data-view=customers]` tables). */
export function customerColumns(
	status: TenantStatusTab,
	onAction: (a: LifecycleAction, t: AdminTenant) => void,
	fairUse?: ReadonlyMap<string, { state: FairUseState; exempt: boolean }>, // PIP4: the state badge column, when the engine answers
): Column<AdminTenant>[] {
	const c = t.list.cols;
	const actions: Column<AdminTenant> = {
		id: "actions",
		header: c.actions,
		align: "right",
		cell: (x) => <Actions tenant={x} status={status} onAction={onAction} />,
	};
	if (status === "pending") {
		return [
			{
				id: "business",
				header: c.business,
				rowHeader: true,
				sortValue: (x) => x.name,
				cell: (x) => <Who tenant={x} sub={x.description ?? ""} />,
			},
			{
				id: "owner",
				header: c.owner,
				stackLabel: true,
				cell: (x) => <span className="font-mono text-[12.5px] break-all">{x.owner_emails[0] ?? t.list.noOwner}</span>,
			},
			{
				id: "signedUp",
				header: c.signedUp,
				stackLabel: true,
				sortValue: (x) => x.created_at,
				cell: (x) => <span title={x.created_at}>{formatRelative(x.created_at)}</span>,
			},
			{
				id: "email",
				header: c.email,
				stackLabel: true,
				cell: () => <Badge variant="ok">{t.list.verified}</Badge>,
			},
			actions,
		];
	}
	const customer: Column<AdminTenant> = {
		id: "customer",
		header: c.customer,
		rowHeader: true,
		sortValue: (x) => x.name,
		cell: (x) =>
			x.owner_emails[0] ? (
				<Who tenant={x} sub={`${x.owner_emails[0]} · ${peopleText(x.member_count)}`} />
			) : (
				<Who tenant={x} sub={peopleText(x.member_count)} />
			),
	};
	const plan: Column<AdminTenant> = {
		id: "plan",
		header: c.plan,
		stackLabel: true,
		sortValue: (x) => x.plan_name,
		cell: (x) => <PlanBadge tenant={x} />,
	};
	// PIP4
	const fairState: Column<AdminTenant> = {
		id: "fairUse",
		header: customerLimitsStrings.listCol,
		stackLabel: true,
		cell: (x) => {
			const row = fairUse?.get(x.id);
			if (!row) return <span title={customerLimitsStrings.unknown}>—</span>;
			if (row.exempt)
				return (
					<Badge variant="default" data-testid="fair-use-exempt">
						{customerLimitsStrings.exempt}
					</Badge>
				);
			return <StateBadge state={row.state} />;
		},
	};
	// /PIP4
	if (status === "active") {
		return [
			customer,
			plan,
			...(fairUse ? [fairState] : []), // PIP4
			{
				id: "kbs",
				header: c.kbs,
				stackLabel: true,
				sortValue: (x) => x.corpus_count,
				cell: (x) => <span className="font-mono">{kbsText(x)}</span>,
			},
			{
				id: "questions",
				header: c.questions,
				sortValue: (x) => x.questions_this_month,
				cell: (x) => (
					<UsageMeter
						variant="cell"
						label={fill(`${c.questions}, {name}`, { name: x.name })}
						used={x.questions_this_month}
						limit={x.monthly_questions}
					/>
				),
			},
			{
				id: "activated",
				header: c.activated,
				stackLabel: true,
				sortValue: (x) => x.activated_at ?? "",
				cell: (x) => (
					<div className="min-w-0">
						<span>{formatShortDate(x.activated_at)}</span>
						{x.activation_note ? (
							<small className="block text-[12px] break-words text-muted-foreground">{x.activation_note}</small>
						) : null}
					</div>
				),
			},
			actions,
		];
	}
	return [
		customer,
		plan,
		{
			id: "status",
			header: c.status,
			stackLabel: true,
			cell: (x) => (
				<div className="flex flex-wrap items-center gap-2">
					<StatusBadge status="suspended" />
					<Link
						to="/admin/audit"
						search={historySearch(x)}
						aria-label={fill(t.list.historyLabel, { name: x.name })}
						className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-brand-text"
					>
						<History className="size-3.5" aria-hidden />
						{t.list.history}
					</Link>
				</div>
			),
		},
		{
			id: "note",
			header: c.note,
			stackLabel: true,
			cell: (x) => <span className="break-words">{x.activation_note ?? "—"}</span>,
		},
		actions,
	];
}
