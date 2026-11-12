import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Home, Plus } from "lucide-react";
import { useMemo } from "react";
import { EmptyState } from "@/components/patterns/EmptyState";
import { Segmented } from "@/components/patterns/Segmented";
import { useSwitchScope } from "@/components/shell/ScopeSwitcher";
import { Button, buttonVariants } from "@/components/ui/button";
import { tenantPlanQuery } from "@/features/customers/api";
import { ADD_CUSTOMER } from "@/features/customers/first-customer";
import { activeCustomersQuery, operatorWorkspaceQuery, planMeQuery } from "@/features/kbs/api";
import { perDay, usageQuery, useAllCorpora } from "@/features/usage/api";
import type { Corpus } from "@/lib/api/queries";
import { firstName, operatorWorkspace, type Principal } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { useDocumentTitle } from "@/lib/branding/useDocumentTitle";
import { formatNumber } from "@/lib/format/number";
import { lastDays, OVERVIEW_RANGES, type OverviewRange, previousDays, previousInstants } from "@/lib/format/range";
import { fill } from "@/lib/strings/en";
import { overviewStrings as t } from "@/lib/strings/overview";
import { AdminChecklist, adminChecklistSteps } from "./AdminChecklist";
import { pendingReviewCountQuery, pendingSignupsQuery, queryCountQuery, trendsRangeQuery } from "./api";
import { KnowledgeGapsCard } from "./KnowledgeGapsCard";
import { KpiCards, type KpiData } from "./KpiCards";
import { averageConfidence, greetingKey, plural, sumDocuments, totalTokens } from "./kpis";
import { PendingSignups } from "./PendingSignups";
import { QuestionsChart } from "./QuestionsChart";
import { RecentJobsCard } from "./RecentJobsCard";
import { RecentQuestionsCard } from "./RecentQuestionsCard";
import { ServiceHealth } from "./ServiceHealth";
import { checklistSteps, SetupChecklist, useChecklistFlags } from "./SetupChecklist";
import { SourceTotals } from "./SourceTotals";
import { TopCustomers } from "./TopCustomers";
import { YourKbsCard } from "./YourKbsCard";

const route = getRouteApi("/_app/");

/** `/` (mockup `[data-view=overview]`): the super admin's engine or the tenant's workspace. */
export function OverviewPage() {
	const principal = usePrincipal();
	if (!principal) return null;
	return <Overview principal={principal} />;
}

function Overview({ principal }: { principal: Principal }) {
	useDocumentTitle(t.title);
	const admin = principal.view === "admin";
	const { range = "7d" } = route.useSearch();
	const navigate = useNavigate();
	// One "now" per range choice keeps every query key stable while the page is open.
	const r = useMemo(() => {
		const cur = lastDays(range);
		return {
			days: cur.days,
			prevDays: previousDays(cur.days),
			instants: cur.instants,
			prevInstants: previousInstants(cur.instants),
		};
	}, [range]);
	const period = t.period[range];

	// The operator view reports on customers: usage of the operator's own workspace is left out
	// (the raw Usage page still shows it, labelled). Inside a workspace there is nothing to leave out.
	const customersOnly = admin ? { exclude_operator: true } : {};
	const bySource = useQuery(usageQuery({ group_by: "source", ...r.days, ...customersOnly }));
	const bySourcePrev = useQuery(usageQuery({ group_by: "source", ...r.prevDays, ...customersOnly }));
	const byDay = useQuery(usageQuery({ group_by: "day", ...r.days, ...customersOnly }));
	const trends = useQuery(trendsRangeQuery(r.instants));
	const trendsPrev = useQuery(trendsRangeQuery(r.prevInstants));
	const corpora = useAllCorpora();
	const reviews = useQuery(pendingReviewCountQuery);
	const customers = useQuery({ ...activeCustomersQuery, enabled: admin });
	const workspace = useQuery({ ...operatorWorkspaceQuery, enabled: admin });
	const pending = useQuery({ ...pendingSignupsQuery, enabled: admin });
	const count = useQuery({ ...queryCountQuery, enabled: !admin });
	const plan = useQuery({ ...planMeQuery, enabled: !admin });

	const ws = workspace.data ?? null;
	const wsPlan = useQuery({ ...tenantPlanQuery(ws?.id ?? ""), enabled: admin && !!ws });
	const canSwitch = operatorWorkspace(principal) !== null;
	const switchScope = useSwitchScope();

	const items: Corpus[] = useMemo(() => corpora.data?.items ?? [], [corpora.data]);
	const kbNames = useMemo(() => new Map(items.map((c) => [c.id, c])), [items]);
	const docs = sumDocuments(items);
	const kbCount = corpora.data?.total ?? 0;
	const tenantId = principal.tenant?.id ?? "admin";
	const flags = useChecklistFlags(tenantId);

	const usageError = bySource.error ?? bySourcePrev.error;
	const trendsError = trends.error ?? trendsPrev.error;
	const kpis: KpiData = {
		questions:
			bySource.data && bySourcePrev.data
				? { now: bySource.data.totals.requests, prev: bySourcePrev.data.totals.requests }
				: null,
		confidence:
			trends.data && trendsPrev.data
				? { now: averageConfidence(trends.data.data), prev: averageConfidence(trendsPrev.data.data) }
				: null,
		tokens:
			bySource.data && bySourcePrev.data
				? {
						now: totalTokens(bySource.data.totals) ?? 0,
						costNow: bySource.data.totals.cost_usd,
						costPrev: bySourcePrev.data.totals.cost_usd,
					}
				: null,
		documents: corpora.data ? { total: docs, kbs: kbCount, waitingReview: reviews.data?.total ?? null } : null,
	};

	// The operator view is "fresh" while there is no customer and no knowledge base anywhere (the
	// operator's own workspace is not a customer, so it does not count).
	const firstRun = admin
		? customers.data?.total === 0 && corpora.data?.total === 0
		: corpora.data != null && corpora.data.total === 0;

	const greeting = fill(t.greeting[greetingKey(new Date().getHours())], { name: firstName(principal) });
	const pendingTotal = pending.data?.total;
	const subtitle = admin
		? customers.data && corpora.data && pendingTotal != null
			? fill(t.subAdmin, {
					customers: plural(t.customers, customers.data.total, formatNumber(customers.data.total)),
					kbs: plural(t.kbs, kbCount, formatNumber(kbCount)),
					pending: plural(t.signups, pendingTotal, formatNumber(pendingTotal)),
				})
			: null
		: bySource.data && corpora.data
			? fill(t.subTenant, {
					tenant: principal.tenant?.name ?? "",
					questions: plural(
						t.questionsWord,
						bySource.data.totals.requests,
						formatNumber(bySource.data.totals.requests),
					),
					period,
					kbs: plural(t.kbs, kbCount, formatNumber(kbCount)),
				})
			: null;

	const steps = checklistSteps({
		kbs: corpora.data?.total,
		documents: corpora.data ? docs : undefined,
		questions: count.isError ? null : count.data?.count,
		widgetKeys: plan.isError ? null : plan.data?.usage.widget_keys,
		agentDone: flags.agentDone,
	});
	const adminChecklist =
		admin && ws && !flags.hidden ? (
			<AdminChecklist
				steps={adminChecklistSteps({
					workspace: ws,
					widgetKeys: wsPlan.isError ? null : wsPlan.data?.usage.widget_keys,
					customers: customers.data?.total,
				})}
				workspaceName={ws.name}
				canSwitch={canSwitch}
				onHide={flags.hide}
			/>
		) : null;
	const checklist =
		!admin && !flags.hidden ? (
			<SetupChecklist
				tenant={principal.tenant?.name ?? ""}
				steps={steps}
				onMarkAgentDone={flags.markAgentDone}
				onHide={flags.hide}
			/>
		) : null;

	const header = (
		<div className="flex flex-wrap items-end justify-between gap-4">
			<div className="min-w-0">
				<h1
					tabIndex={-1}
					className="font-mono text-[11px] font-medium tracking-[.08em] text-muted-foreground uppercase"
				>
					{t.title}
				</h1>
				<p data-testid="greeting" className="mt-1 mb-0 font-display text-2xl font-bold break-words">
					{greeting}
				</p>
				{subtitle ? (
					<p data-testid="overview-sub" className="mt-1 mb-0 text-foreground-2">
						{subtitle}
					</p>
				) : null}
			</div>
			{firstRun ? null : (
				<Segmented<OverviewRange>
					label={t.range}
					value={range}
					onChange={(v) => void navigate({ to: "/", search: { range: v }, replace: true })}
					options={OVERVIEW_RANGES.map((v) => ({ value: v, label: t.ranges[v] }))}
				/>
			)}
		</div>
	);

	if (firstRun) {
		return (
			<div className="grid gap-4">
				{header}
				<EmptyState
					title={admin ? t.firstRun.adminTitle : t.firstRun.tenantTitle}
					body={admin ? t.firstRun.adminBody : t.firstRun.tenantBody}
					primaryAction={
						admin ? (
							<Link {...ADD_CUSTOMER} className={buttonVariants({ variant: "default" })}>
								<Plus aria-hidden />
								{t.firstRun.adminAction}
							</Link>
						) : (
							<Link to="/kbs" className={buttonVariants({ variant: "default" })}>
								<Plus aria-hidden />
								{t.firstRun.tenantAction}
							</Link>
						)
					}
					secondaryAction={
						admin && canSwitch ? (
							<Button variant="outline" onClick={() => void switchScope("workspace", "newKb")}>
								<Home aria-hidden />
								{t.firstRun.adminOwn}
							</Button>
						) : undefined
					}
				/>
				<div className="grid items-start gap-4 min-[1101px]:grid-cols-2">
					{admin ? (
						<>
							{adminChecklist}
							<PendingSignups />
							<ServiceHealth />
						</>
					) : (
						checklist
					)}
				</div>
			</div>
		);
	}

	return (
		<div className="grid gap-4">
			{header}
			<KpiCards
				data={kpis}
				errors={{ usage: usageError, trends: trendsError, corpora: corpora.error }}
				vsPrevious={t.vsPrevious[range]}
				onRetry={{
					usage: () => void Promise.all([bySource.refetch(), bySourcePrev.refetch()]),
					trends: () => void Promise.all([trends.refetch(), trendsPrev.refetch()]),
					corpora: () => void corpora.refetch(),
				}}
			/>
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
				<QuestionsChart
					points={byDay.data ? perDay(byDay.data.rows, r.days) : undefined}
					period={period}
					pending={byDay.isPending}
					error={byDay.error}
					onRetry={() => void byDay.refetch()}
					footer={bySource.data ? <SourceTotals rows={bySource.data.rows} /> : null}
				/>
				<div className="grid content-start gap-4">
					{admin ? (
						<>
							{adminChecklist}
							<PendingSignups />
							<ServiceHealth />
						</>
					) : (
						<>
							{checklist}
							<RecentQuestionsCard kbNames={kbNames} />
						</>
					)}
				</div>
			</div>
			<div className="grid items-start gap-4 min-[861px]:grid-cols-3">
				<KnowledgeGapsCard kbNames={kbNames} />
				<RecentJobsCard kbNames={kbNames} />
				{admin ? (
					<TopCustomers range={r.days} period={period} />
				) : (
					<YourKbsCard corpora={items} range={r.days} period={period} />
				)}
			</div>
		</div>
	);
}
