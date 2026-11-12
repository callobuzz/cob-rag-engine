import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { FormAlert } from "@/components/auth/FormAlert";
import { CopyButton } from "@/components/patterns/CopyButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { fastBenchmarksQuery, fastTags, findResult, pct, perThousand, seconds } from "@/features/benchmarks/api";
import { BudgetCallout, budgetOffer } from "@/features/benchmarks/BudgetCallout";
import { BelowBar, TagBadges, ThinkingNote } from "@/features/benchmarks/parts";
import { Panel } from "@/features/kbs/ui";
import type { EngineInfo } from "@/lib/api/accounts-types";
import { useCurrency } from "@/lib/format/currency";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";

/** The engine's fast model (from .env) against the benchmark rows: its numbers, or "not benchmarked". */
export function FastModelCard({ llm }: { llm: EngineInfo["llm"] }) {
	const q = useQuery(fastBenchmarksQuery);
	const currency = useCurrency();
	const hosts = llm?.fast_provider_order ?? [];
	const bench = q.data?.benchmarks;
	const row = findResult(bench?.models, llm?.fast_model, hosts);
	const rec = bench?.models.find((m) => m.key === bench.recommendations.default);
	const recDiffers = rec && (!row || rec.key !== row.key);
	const offer = !llm?.fast_model ? null : budgetOffer(bench, { model: llm?.fast_model, hosts });
	return (
		<Panel title={t.engine.title} sub={t.engine.sub}>
			{q.isPending ? (
				<CardSkeleton lines={4} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.engine.error} onRetry={() => void q.refetch()} />
				</div>
			) : !q.data.available || !bench ? (
				<div className="p-4">
					<FormAlert tone="info">{t.engine.unavailable}</FormAlert>
				</div>
			) : (
				<>
					<KeyValue
						rows={[
							{ label: t.engine.model, value: llm?.fast_model ?? "—", muted: !llm?.fast_model },
							{
								label: t.engine.hosts,
								value: hosts.length > 0 ? hosts.join(", ") : t.engine.noHosts,
								muted: hosts.length === 0,
							},
							...(row
								? [
										{ label: t.engine.accuracy, value: pct(row.overall) },
										{ label: t.engine.p90, value: seconds(row.latency_s.p90) },
										{ label: t.engine.cost, value: perThousand(row.usd_per_1000_labels, currency) },
									]
								: []),
							...(recDiffers ? [{ label: t.engine.recommended, value: rec.key }] : []),
						]}
					/>
					<div className="grid gap-2 border-t border-border p-3">
						{!llm?.fast_model ? (
							<p className="m-0 text-[12.5px] text-muted-foreground">{t.engine.none}</p>
						) : row ? (
							<>
								<div className="flex flex-wrap items-center gap-1.5">
									<TagBadges tags={fastTags(bench, row)} />
									<ThinkingNote model={row} />
								</div>
								<BelowBar model={row} />
							</>
						) : (
							<FormAlert tone="warn">{t.engine.notBenchmarked}</FormAlert>
						)}
						{offer ? (
							<>
								<BudgetCallout
									offer={offer}
									action={
										<CopyButton
											value={fill("RAG_LLM_FAST_MODEL={model}", { model: offer.budget.model })}
											label={t.budgetCallout.engineCopy}
											ariaLabel={t.budgetCallout.engineCopyLabel}
										/>
									}
								/>
								<p className="m-0 text-[12.5px] text-muted-foreground">
									{fill(t.budgetCallout.engineBody, { model: offer.budget.model })}
								</p>
							</>
						) : null}
						<Link to="/benchmarks" className="text-[13px] text-primary hover:underline">
							{t.engine.viewAll}
						</Link>
					</div>
				</>
			)}
		</Panel>
	);
}
