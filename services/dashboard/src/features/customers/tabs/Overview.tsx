import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { History } from "lucide-react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { UsageMeter } from "@/components/patterns/UsageMeter";
import { Panel } from "@/features/kbs/ui";
import type { AdminTenant } from "@/lib/api/accounts-types";
import { formatDate, formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { plansStrings, customersStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { tenantPlanQuery } from "../api";
import { historySearch } from "../CustomerRow";

/** Overview: plan and usage (`GET /admin/tenants/{id}/plan`, the same meters as D2) and details. */
export function OverviewTab({ tenant }: { tenant: AdminTenant }) {
	const plan = useQuery(tenantPlanQuery(tenant.id));
	const p = plan.data;
	return (
		<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
			<Panel
				title={t.detail.planTitle}
				sub={p ? fill(t.detail.planSub, { date: formatDate(p.resets_at) }) : undefined}
				aside={p ? <b className="text-[15px]">{p.plan.name}</b> : null}
			>
				{plan.isPending ? (
					<TableSkeleton rows={4} />
				) : plan.isError ? (
					<div className="p-4">
						<ErrorState error={plan.error} title={t.detail.planError} onRetry={() => void plan.refetch()} />
					</div>
				) : (
					<div data-testid="plan-usage">
						{p?.plan.description ? (
							<p className="m-0 border-b border-border px-4 py-2.5 text-[13px] text-foreground-2">
								{fill(t.detail.priceNote, { note: p.plan.description })}
							</p>
						) : null}
						<UsageMeter
							label={plansStrings.usage.questions}
							used={p?.usage.questions ?? 0}
							limit={p?.limits.monthly_questions ?? null}
						/>
						<UsageMeter
							label={plansStrings.usage.kbs}
							used={p?.usage.knowledge_bases ?? 0}
							limit={p?.limits.max_knowledge_bases ?? null}
						/>
						<UsageMeter
							label={plansStrings.usage.documents}
							used={p?.usage.documents ?? 0}
							limit={p?.limits.max_documents ?? null}
						/>
						<UsageMeter
							label={plansStrings.usage.widgetKeys}
							used={p?.usage.widget_keys ?? 0}
							limit={p?.limits.max_widget_keys ?? null}
						/>
					</div>
				)}
			</Panel>
			<Panel title={t.detail.detailsTitle}>
				<KeyValue
					rows={[
						{ label: t.detail.status, value: <StatusBadge status={tenant.status} /> },
						{ label: t.detail.created, value: formatDateTime(tenant.created_at) },
						{ label: t.detail.activated, value: formatDateTime(tenant.activated_at) },
						{ label: t.detail.note, value: tenant.activation_note ?? "—", muted: !tenant.activation_note },
						{
							label: t.detail.origin,
							value: tenant.created_by_user_id ? t.detail.signedUp : t.detail.addedByHand,
						},
						{ label: t.detail.owners, value: tenant.owner_emails.join(", ") || "—" },
						{ label: t.detail.people, value: formatNumber(tenant.member_count) },
					]}
				/>
				<div className="border-t border-border px-4 py-3">
					<Link
						to="/admin/audit"
						search={historySearch(tenant)}
						className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-brand-text"
					>
						<History className="size-4" aria-hidden />
						{t.detail.history}
					</Link>
				</div>
			</Panel>
		</div>
	);
}
