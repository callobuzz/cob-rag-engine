import { useQuery } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { Download } from "lucide-react";
import { useId, useMemo } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Segmented } from "@/components/patterns/Segmented";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Kpi, Panel } from "@/features/kbs/ui";
import { useAllCorpora, useWidgetKeyNames } from "@/features/usage/api";
import { downloadText } from "@/features/usage/csv";
import { sourceLabel, widgetKeyId } from "@/features/usage/source-labels";
import { toApiError } from "@/lib/api/errors";
import { formatCost } from "@/lib/format/cost";
import { type Currency, setCurrency, useCurrency } from "@/lib/format/currency";
import { formatDate } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { checkRange, dayMs, USAGE_PRESETS, type UsagePreset, usageRange } from "@/lib/format/range";
import { tenantLabel } from "@/lib/format/tenant";
import { costStrings as t } from "@/lib/strings/costs";
import { fill } from "@/lib/strings/en";
import { usageStrings as u } from "@/lib/strings/overview";
import { COST_GROUPS, type CostGroup, type CostRow, costsQuery, fetchCostsCsv } from "./api";
import { BalanceCard } from "./BalanceCard";
import { CostTable, type LabelledCostRow } from "./CostTable";
import { ProviderBalancesPanel } from "./ProviderBalancesPanel"; // BIL1
import type { CostsSearch } from "./search";

const route = getRouteApi("/_app/admin/costs");

const BREAKDOWN_KEY = {
	tenant: "by_tenant",
	corpus: "by_corpus",
	model: "by_model",
	source: "by_source",
	payer: "by_payer",
} as const;

/**
 * `/admin/costs` (super admin): what the platform spends on models. Totals for today, this month and
 * the chosen range; the OpenRouter balance when the engine's key is one; the range broken down by
 * customer, knowledge base, model, source and payer; and the engine's CSV. A request nobody could
 * price reads "price unknown" or "≥ …", never ₹0.00.
 */
export function CostsPage() {
	const id = useId();
	const search = route.useSearch();
	const navigate = useNavigate();
	const currency = useCurrency();
	const preset: UsagePreset = search.range ?? "month";
	const range = useMemo(
		() => usageRange(preset, { from: search.from, to: search.to }),
		[preset, search.from, search.to],
	);
	const problem = checkRange(range);
	const q = useQuery({ ...costsQuery({ start: range.start, end: range.end }), enabled: !problem });
	const corpora = useAllCorpora();
	const data = q.data;
	const hasWidget = (data?.by_source ?? []).some((r) => widgetKeyId(r.key));
	const widgetNames = useWidgetKeyNames(corpora.data?.items ?? [], hasWidget);

	const set = (patch: Partial<CostsSearch>) =>
		void navigate({ to: "/admin/costs", search: { ...search, ...patch }, replace: true });

	const nameOf = (group: CostGroup, r: CostRow): string => {
		const key = r.key;
		if (group === "tenant") {
			return key
				? tenantLabel({
						name: r.label ?? fill(u.unknownCustomer, { id: key.slice(0, 8) }),
						is_operator: !!r.is_operator,
					})
				: u.noCustomer;
		}
		if (group === "corpus") return key ? (r.label ?? fill(u.deletedKb, { id: key.slice(0, 8) })) : "—";
		if (group === "model") return key || t.noModel;
		if (group === "payer") return key === "operator" || key === "byok" ? t.payers[key] : (key ?? "—");
		return sourceLabel(key, widgetNames);
	};

	const exportCsv = async () => {
		try {
			const text = await fetchCostsCsv({ start: range.start, end: range.end });
			const file = `costs-${range.start}-to-${range.end}.csv`;
			downloadText(text, file);
			toast.success(fill(t.page.exported, { file }));
		} catch (e) {
			toast.error(toApiError(e).detail || t.page.error);
		}
	};

	const empty = !!data && data.range.requests === 0;
	const unpriced = data?.range.unpriced_requests ?? 0;

	return (
		<div className="grid gap-4">
			<PageHeader
				title={t.page.title}
				description={t.page.lead}
				actions={
					<>
						<Segmented<Currency>
							label={t.page.currency}
							value={currency}
							onChange={setCurrency}
							options={[
								{ value: "INR", label: "₹ INR" },
								{ value: "USD", label: "$ USD" },
							]}
						/>
						<Button onClick={() => void exportCsv()} disabled={!data || empty}>
							<Download aria-hidden />
							{t.page.export}
						</Button>
					</>
				}
			/>
			<section aria-label={u.filters} className="flex flex-wrap items-end gap-3">
				<div className="grid gap-1.5">
					<Label htmlFor={`${id}-range`}>{u.range}</Label>
					<NativeSelect
						id={`${id}-range`}
						className="w-[170px]"
						value={preset}
						onChange={(e) => {
							const next = e.target.value as UsagePreset;
							set(
								next === "custom"
									? { range: next, from: range.start, to: range.end }
									: { range: next, from: undefined, to: undefined },
							);
						}}
					>
						{USAGE_PRESETS.map((p) => (
							<option key={p} value={p}>
								{u.presets[p]}
							</option>
						))}
					</NativeSelect>
				</div>
				{preset === "custom" ? (
					<>
						<div className="grid gap-1.5">
							<Label htmlFor={`${id}-from`}>{u.from}</Label>
							<Input
								id={`${id}-from`}
								type="date"
								className="w-[160px]"
								value={range.start}
								onChange={(e) => e.target.value && set({ from: e.target.value })}
							/>
						</div>
						<div className="grid gap-1.5">
							<Label htmlFor={`${id}-to`}>{u.to}</Label>
							<Input
								id={`${id}-to`}
								type="date"
								className="w-[160px]"
								value={range.end}
								onChange={(e) => e.target.value && set({ to: e.target.value })}
							/>
						</div>
					</>
				) : null}
				{problem ? (
					<p role="alert" className="m-0 text-[13px] font-semibold text-destructive">
						{u.rangeProblems[problem]}
					</p>
				) : null}
			</section>

			{problem ? null : q.isError ? (
				<ErrorState error={q.error} title={t.page.error} onRetry={() => void q.refetch()} />
			) : !data ? (
				<TableSkeleton rows={5} />
			) : (
				<>
					<div
						className="grid grid-cols-4 gap-4 max-[1100px]:grid-cols-2 max-[600px]:grid-cols-1"
						data-testid="costs-summary"
					>
						<Kpi
							label={t.page.today}
							value={formatCost(data.today.cost_usd, data.today.cost_known, currency, { exact: true })}
							delta={fill(u.summary.tokensInOut, {
								in: formatNumber(data.today.prompt_tokens),
								out: formatNumber(data.today.completion_tokens),
							})}
						/>
						<Kpi
							label={t.page.month}
							value={formatCost(data.month.cost_usd, data.month.cost_known, currency, { exact: true })}
							delta={`${formatNumber(data.month.requests)} ${u.summary.requests.toLowerCase()}`}
						/>
						<Kpi
							label={t.page.range}
							value={formatCost(data.range.cost_usd, data.range.cost_known, currency, { exact: true })}
							delta={
								unpriced > 0
									? fill(t.page.unpricedNote, { n: formatNumber(unpriced) })
									: fill(u.summary.period, {
											from: formatDate(dayMs(range.start)),
											to: formatDate(dayMs(range.end)),
										})
							}
						/>
						{data.openrouter ? <BalanceCard balance={data.openrouter} /> : null}
					</div>
					<p className="m-0 text-[12.5px] text-muted-foreground">
						{fill(t.exactNote, { rate: formatNumber(data.usd_inr_rate) })}
					</p>
					{/* BIL1 providers */}
					<ProviderBalancesPanel />
					{/* /BIL1 providers */}
					{empty ? (
						<EmptyState title={t.page.empty} body={t.page.emptyBody} />
					) : (
						COST_GROUPS.map((group) => {
							const rows: LabelledCostRow[] = data[BREAKDOWN_KEY[group]].map((r) => ({ ...r, name: nameOf(group, r) }));
							return (
								<Panel key={group} title={t.groups[group]}>
									<div className="overflow-x-auto">
										<CostTable
											rows={rows}
											totals={data.range}
											groupLabel={t.groups[group].toLowerCase()}
											currency={currency}
										/>
									</div>
								</Panel>
							);
						})
					)}
				</>
			)}
		</div>
	);
}
