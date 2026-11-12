import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { activeTenantsQuery } from "@/features/kbs/api";
import { KbAvatar } from "@/features/kbs/KbAvatar";
import { Panel } from "@/features/kbs/ui";
import { type UsageRow, usageQuery } from "@/features/usage/api";
import { formatNumber } from "@/lib/format/number";
import type { DayRange } from "@/lib/format/range";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { plural } from "./kpis";
import { HeadLink, List, Muted, Row } from "./ui";

const t = overviewStrings.top;

/** Customers with the most questions in the range, top first (tenant rollups are never null, e0/A1). */
export function topRows(rows: UsageRow[], n = 5): UsageRow[] {
	return rows
		.filter((r) => r.key && r.requests > 0 && !r.is_operator)
		.sort((a, b) => b.requests - a.requests)
		.slice(0, n);
}

export function TopCustomers({ range, period }: { range: DayRange; period: string }) {
	// Customers only: the operator's own workspace is not a customer (and not revenue).
	const usage = useQuery(
		usageQuery({ group_by: "tenant", start: range.start, end: range.end, exclude_operator: true }),
	);
	const tenants = useQuery(activeTenantsQuery);
	const byId = new Map((tenants.data?.items ?? []).map((x) => [x.id, x]));
	const rows = topRows(usage.data?.rows ?? []);
	const error = usage.error ?? tenants.error;
	return (
		<Panel
			title={t.title}
			sub={fill(t.sub, { period })}
			aside={
				<HeadLink to="/admin/customers" label={t.allLabel}>
					{t.all}
				</HeadLink>
			}
		>
			{usage.isPending || tenants.isPending ? (
				<TableSkeleton rows={3} />
			) : error ? (
				<div className="p-4">
					<ErrorState
						error={error}
						title={t.error}
						onRetry={() => void Promise.all([usage.refetch(), tenants.refetch()])}
					/>
				</div>
			) : rows.length === 0 ? (
				<Muted>{fill(t.none, { period })}</Muted>
			) : (
				<List label={t.title}>
					{rows.map((r) => {
						const id = r.key ?? "";
						const c = byId.get(id);
						const name = c?.name ?? `${id.slice(0, 8)}…`;
						return (
							<Row
								key={id}
								testId="top-customer"
								lead={<KbAvatar kb={{ id, name }} size={28} />}
								title={
									<Link
										to="/admin/customers/$tenantId"
										params={{ tenantId: id }}
										className="text-foreground no-underline hover:underline"
									>
										{name}
									</Link>
								}
								sub={
									c
										? fill(t.line, {
												plan: c.plan_name,
												kbs: plural(overviewStrings.kbs, c.corpus_count, formatNumber(c.corpus_count)),
											})
										: undefined
								}
								trail={<b className="font-mono font-medium tabular-nums">{formatNumber(r.requests)}</b>}
							/>
						);
					})}
				</List>
			)}
		</Panel>
	);
}
