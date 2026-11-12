import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Download, MessageSquare } from "lucide-react";
import { useMemo } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Segmented } from "@/components/patterns/Segmented";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button, buttonVariants } from "@/components/ui/button";
import { activeTenantsQuery } from "@/features/kbs/api";
import { Kpi, Panel } from "@/features/kbs/ui";
import { type Corpus, infoQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { formatCost } from "@/lib/format/cost";
import { type Currency, setCurrency, useCurrency } from "@/lib/format/currency";
import { formatDate } from "@/lib/format/date";
import { formatCompact, formatNumber } from "@/lib/format/number";
import { checkRange, dayMs, type UsagePreset, usageRange } from "@/lib/format/range";
import { tenantLabel } from "@/lib/format/tenant";
import { fill } from "@/lib/strings/en";
import { usageStrings as t } from "@/lib/strings/overview";
import { perDay, type UsageGroup, type UsageRow, usageQuery, useAllCorpora, useWidgetKeyNames } from "./api";
import { downloadText, usageCsv, usageCsvName } from "./csv";
import { sourceLabel, widgetKeyId } from "./source-labels";
import { UsageChart } from "./UsageChart";
import { UsageFilters } from "./UsageFilters";
import { type LabelledRow, UsageTable } from "./UsageTable";

const route = getRouteApi("/_app/usage");

type SearchPatch = Partial<{
	range: UsagePreset;
	from: string;
	to: string;
	kb: string;
	tenant: string;
	group: UsageGroup;
	own: number;
}>;

/** `/usage`: filters → summary strip → per-day chart → grouped table with totals and CSV. */
export function UsagePage() {
	const principal = usePrincipal();
	const admin = principal?.view === "admin";
	const search = route.useSearch();
	const navigate = useNavigate();
	const currency = useCurrency();
	const preset: UsagePreset = search.range ?? "30d";
	const group: UsageGroup = search.group === "tenant" && !admin ? "corpus" : (search.group ?? "corpus");
	const range = useMemo(
		() => usageRange(preset, { from: search.from, to: search.to }),
		[preset, search.from, search.to],
	);
	const problem = checkRange(range);
	const tenant = admin ? search.tenant : undefined;
	// Raw usage shows everything, the operator's own workspace labelled; `own=0` leaves it out.
	const excludeOwn = admin && search.own === 0;
	const base = {
		start: range.start,
		end: range.end,
		corpus_id: search.kb,
		tenant_id: tenant,
		...(excludeOwn ? { exclude_operator: true } : {}),
	};

	const corpora = useAllCorpora();
	// COB answers from a knowledge base no workspace owns; its rows are labelled COB, not "deleted".
	const info = useQuery(infoQuery);
	const cobCorpusId = info.data?.features.cob?.corpus_id ?? null;
	const tenants = useQuery({ ...activeTenantsQuery, enabled: admin });
	const byDay = useQuery({ ...usageQuery({ ...base, group_by: "day" }), enabled: !problem });
	const grouped = useQuery({ ...usageQuery({ ...base, group_by: group }), enabled: !problem });

	const allKbs: Corpus[] = useMemo(() => corpora.data?.items ?? [], [corpora.data]);
	const kbById = useMemo(() => new Map(allKbs.map((c) => [c.id, c])), [allKbs]);
	const tenantName = useMemo(() => new Map((tenants.data?.items ?? []).map((x) => [x.id, x.name])), [tenants.data]);
	const inScope = allKbs.filter((c) => (!search.kb || c.id === search.kb) && (!tenant || c.tenant_id === tenant));
	const hasWidget = group === "source" && (grouped.data?.rows ?? []).some((r) => widgetKeyId(r.key));
	const widgetNames = useWidgetKeyNames(inScope, hasWidget);

	const set = (patch: SearchPatch) => void navigate({ to: "/usage", search: { ...search, ...patch }, replace: true });

	const label = (r: UsageRow): string => {
		const key = r.key;
		if (group === "day") return key ? formatDate(dayMs(key.slice(0, 10))) : "—";
		if (group === "corpus") {
			if (key && key === cobCorpusId) return t.cobKb;
			return key ? (kbById.get(key)?.name ?? fill(t.deletedKb, { id: key.slice(0, 8) })) : "—";
		}
		if (group === "model") return key || t.noModel;
		if (group === "tenant") {
			if (!key) return t.noCustomer;
			const name = tenantName.get(key) ?? fill(t.unknownCustomer, { id: key.slice(0, 8) });
			return r.is_operator ? tenantLabel({ name, is_operator: true }) : name;
		}
		return sourceLabel(key, widgetNames);
	};
	// The engine already reports a customer only what their own LLM key paid (what the operator's
	// key paid never leaves the server), so the cost columns are shown as they come.
	const rows: LabelledRow[] = (grouped.data?.rows ?? []).map((r) => ({ ...r, label: label(r) }));
	const totals: LabelledRow | null = grouped.data ? { ...grouped.data.totals, key: null, label: t.table.total } : null;
	const showCost = admin || (!!totals && (totals.cost_usd > 0 || (totals.unpriced_requests ?? 0) > 0));
	const empty = !!grouped.data && grouped.data.totals.requests === 0;
	const groupLabel = t.groups[group].toLowerCase();

	const exportCsv = () => {
		if (!totals) return;
		const file = usageCsvName(range.start, range.end, group);
		downloadText(usageCsv(rows, totals, currency), file);
		toast.success(fill(t.exported, { file }));
	};

	return (
		<div className="grid gap-4">
			<PageHeader
				title={t.title}
				description={t.lead}
				actions={
					<>
						<Segmented<Currency>
							label={t.currency}
							value={currency}
							onChange={setCurrency}
							options={[
								{ value: "INR", label: "₹ INR" },
								{ value: "USD", label: "$ USD" },
							]}
						/>
						<Button onClick={exportCsv} disabled={!totals || empty}>
							<Download aria-hidden />
							{t.export}
						</Button>
					</>
				}
			/>
			<UsageFilters
				value={{ preset, range, kb: search.kb, tenant, group, excludeOwn }}
				admin={admin}
				hasOwn={(tenants.data?.items ?? []).some((x) => x.is_operator)}
				corpora={allKbs}
				tenants={tenants.data?.items ?? []}
				problem={problem}
				onChange={set}
			/>
			{problem ? null : grouped.isError ? (
				<ErrorState error={grouped.error} title={t.error} onRetry={() => void grouped.refetch()} />
			) : empty ? (
				<EmptyState
					title={t.empty}
					body={t.emptyBody}
					primaryAction={
						<Link to="/playground" className={buttonVariants({ variant: "default" })}>
							<MessageSquare aria-hidden />
							{t.emptyAction}
						</Link>
					}
				/>
			) : (
				<>
					<div className="grid grid-cols-3 gap-4 max-[860px]:grid-cols-1" data-testid="usage-summary">
						<Kpi
							label={t.summary.requests}
							value={grouped.data ? formatNumber(grouped.data.totals.requests) : "…"}
							delta={fill(t.summary.period, {
								from: formatDate(dayMs(range.start)),
								to: formatDate(dayMs(range.end)),
							})}
						/>
						<Kpi
							label={t.summary.tokens}
							value={
								grouped.data
									? formatCompact(grouped.data.totals.prompt_tokens + grouped.data.totals.completion_tokens)
									: "…"
							}
							delta={
								grouped.data
									? fill(t.summary.tokensInOut, {
											in: formatNumber(grouped.data.totals.prompt_tokens),
											out: formatNumber(grouped.data.totals.completion_tokens),
										})
									: null
							}
						/>
						{showCost ? (
							<Kpi
								label={admin ? t.summary.cost : t.summary.spend}
								value={totals ? formatCost(totals.cost_usd, totals.cost_known, currency, { exact: true }) : "…"}
								delta={admin ? t.summary.costNote : t.summary.spendNote}
							/>
						) : (
							<Kpi label={t.summary.spend} value="—" delta={t.noSpend} />
						)}
					</div>
					<UsageChart
						range={range}
						points={byDay.data ? perDay(byDay.data.rows, range) : undefined}
						pending={byDay.isPending}
						error={byDay.error}
						onRetry={() => void byDay.refetch()}
					/>
					<Panel title={fill(t.table.title, { group: groupLabel })}>
						{grouped.isPending || !totals ? (
							<TableSkeleton rows={5} />
						) : (
							<div className="overflow-x-auto">
								<UsageTable
									key={group}
									rows={rows}
									totals={totals}
									groupLabel={groupLabel}
									currency={currency}
									showCost={showCost}
									costHeader={admin ? t.table.cost : t.table.spend}
									initialSort={group === "day" ? { id: "label", dir: "asc" } : { id: "requests", dir: "desc" }}
								/>
							</div>
						)}
					</Panel>
				</>
			)}
		</div>
	);
}
