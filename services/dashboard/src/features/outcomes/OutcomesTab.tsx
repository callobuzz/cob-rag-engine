import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Segmented } from "@/components/patterns/Segmented";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { RANGES, type Range, rangeStart } from "@/features/insights/api";
import { ChartOrTable, Columns, SimpleTable } from "@/features/insights/charts";
import type { ScopeProps } from "@/features/insights/InsightsPage";
import { FilterRow, Note } from "@/features/insights/ui";
import { Kpi, Panel } from "@/features/kbs/ui";
import { formatCost } from "@/lib/format/cost";
import { formatNumber, formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import { leadsStrings } from "@/lib/strings/leads";
import { reviewStrings } from "@/lib/strings/review";
import { type Outcomes, outcomesQuery } from "./api";

// The engine defaults a missing `from` to 30 days, so "All time" sends an explicit early bound.
const ALL_TIME_FROM = "2000-01-01T00:00:00.000Z";
const t = reviewStrings.outcomes;
const rate = (v: number | null | undefined) => (v == null ? "—" : formatPercent(v));
const count = (v: number | null | undefined) => (v == null ? "—" : formatNumber(v));

type Item = { key: string; count: number };

/** A bar chart of counted keys; "Show as table" swaps in the same numbers as a table. */
function CountChart({ title, keyLabel, items, sub }: { title: string; keyLabel: string; items: Item[]; sub?: string }) {
	return (
		<Panel title={title} sub={sub}>
			<div className="p-4 pb-2">
				{items.length === 0 ? (
					<p className="m-0 text-[13px] text-muted-foreground">{t.noData}</p>
				) : (
					<ChartOrTable
						tableLabel={title}
						table={
							<SimpleTable
								caption={title}
								headers={[keyLabel, t.count]}
								rows={items.map((i) => ({ key: i.key, cells: [i.key, formatNumber(i.count)] }))}
							/>
						}
						chart={
							<Columns
								points={items.map((i) => ({ key: i.key, label: i.key, value: i.count }))}
								summary={`${title}: ${items.map((i) => `${i.key} ${i.count}`).join(", ")}`}
								format={(v) => formatNumber(v)}
							/>
						}
					/>
				)}
			</div>
		</Panel>
	);
}

function UnansweredList({ o, kbId }: { o: Outcomes; kbId: string | undefined }) {
	return (
		<Panel title={t.topUnanswered}>
			{o.top_unanswered.length === 0 ? (
				<p className="m-0 p-4 text-[13px] text-muted-foreground">{t.noData}</p>
			) : (
				<ul className="m-0 grid list-none gap-1 p-4">
					{o.top_unanswered.map((u) => (
						<li key={u.key} className="flex items-baseline justify-between gap-3">
							{kbId ? (
								<Link
									to="/review"
									search={{ kb: kbId, kind: "no_answer" }}
									className="min-w-0 break-words font-medium underline-offset-4 hover:underline"
								>
									{u.key}
								</Link>
							) : (
								<span className="min-w-0 break-words">{u.key}</span>
							)}
							<span className="tabular-nums text-muted-foreground">{formatNumber(u.count)}</span>
						</li>
					))}
				</ul>
			)}
		</Panel>
	);
}

/**
 * What the assistant achieved over a period, for one knowledge base or every one in scope:
 * resolved without a person, handed to a person, couldn't answer, leads, and real cost per
 * conversation (test runs excluded). Figures the engine cannot give yet (no handoff or lead
 * table) show a dash and the engine's note, never a made-up zero.
 */
export function OutcomesTab({ kb, corpusId }: ScopeProps) {
	const [range, setRange] = useState<Range>("30d");
	const q = useQuery(outcomesQuery(corpusId, { from: rangeStart(range) ?? ALL_TIME_FROM }));
	const o = q.data;
	const picker = (
		<FilterRow className="px-0 py-0">
			<Segmented
				label={insightsStrings.rangeLabel}
				value={range}
				options={RANGES.map((r) => ({ value: r, label: insightsStrings.ranges[r] }))}
				onChange={setRange}
			/>
		</FilterRow>
	);
	if (q.isError) {
		return (
			<>
				{picker}
				<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
			</>
		);
	}
	if (!o) {
		return (
			<>
				{picker}
				<CardSkeleton lines={6} />
			</>
		);
	}
	if (o.conversations === 0 && !(o.leads ?? 0) && o.cost_usd === 0) {
		return (
			<>
				{picker}
				<EmptyState title={t.empty} />
			</>
		);
	}
	const costTotal = formatCost(o.cost_usd, o.cost_known);
	return (
		<>
			{picker}
			<div
				className="grid grid-cols-1 gap-4 min-[600px]:grid-cols-2 min-[1101px]:grid-cols-3"
				data-testid="outcome-kpis"
			>
				<Kpi
					label={t.resolution}
					value={rate(o.resolution_rate)}
					delta={fill(t.resolutionSentence, { n: formatNumber(o.resolved), total: formatNumber(o.conversations) })}
				/>
				<Kpi
					label={t.handoff}
					value={rate(o.handoff_rate)}
					delta={o.handoffs == null ? t.unavailable : fill(t.handoffSentence, { n: formatNumber(o.handoffs) })}
				/>
				<Kpi
					label={t.noAnswer}
					value={rate(o.no_answer_rate)}
					delta={fill(t.noAnswerSentence, { n: formatNumber(o.no_answer_turns) })}
				/>
				<Kpi
					label={t.leads}
					value={count(o.leads)}
					delta={o.leads == null ? t.unavailable : fill(t.leadsSentence, { p: rate(o.lead_conversion_rate) })}
				/>
				<Kpi
					label={t.cost}
					value={formatCost(o.cost_per_conversation_usd, o.cost_known)}
					delta={fill(t.costSentence, { total: costTotal })}
				/>
				<Kpi
					label={t.found}
					value={rate(o.gate_pass_rate)}
					delta={fill(t.foundSentence, {
						near: formatNumber(o.gate_near_misses ?? 0),
						removed: formatNumber(o.claims_removed),
					})}
				/>
			</div>
			{o.notes?.map((n) => (
				<Note key={n}>{n}</Note>
			))}
			<div className="grid gap-4 min-[1101px]:grid-cols-2">
				<CountChart title={t.languages} keyLabel={t.language} items={o.languages} />
				<CountChart
					title={t.handoffReasons}
					keyLabel={t.reason}
					items={(o.handoff_reasons ?? []).map((i) => ({ ...i, key: leadsStrings.handoffs.reasons[i.key] ?? i.key }))}
				/>
				<CountChart title={t.leadsByPage} keyLabel={t.page} items={o.leads_by_page ?? []} />
				<CountChart title={t.rules} keyLabel={t.rule} items={o.rules_fired} />
			</div>
			<UnansweredList o={o} kbId={corpusId ? kb.id : undefined} />
		</>
	);
}
