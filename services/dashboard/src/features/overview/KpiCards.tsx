import type { LucideIcon } from "lucide-react";
import { BadgeCheck, Cpu, FileText, MessageSquare } from "lucide-react";
import type { ReactNode } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { formatMoney, useCurrency } from "@/lib/format/currency";
import { formatCompact, formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { cn } from "@/lib/utils";
import { type DeltaText, describeDelta, plural, pointsDelta, relativeDelta } from "./kpis";

const k = overviewStrings.kpi;

/** Mockup `.kpi`: icon + label, big value (proportional figures), delta line. */
function KpiCard({
	icon: Icon,
	label,
	value,
	suffix,
	children,
	testId,
}: {
	icon: LucideIcon;
	label: string;
	value: ReactNode;
	suffix?: ReactNode;
	children?: ReactNode;
	testId: string;
}) {
	return (
		<div
			data-testid={testId}
			className="grid content-start gap-1 rounded-lg border border-border bg-card p-4 max-[520px]:p-3"
		>
			<div className="flex items-center gap-2 text-[13px] text-foreground-2 max-[520px]:text-[12px]">
				<Icon className="size-4 text-muted-foreground" aria-hidden />
				{label}
			</div>
			<div className="font-display text-[28px] font-bold tracking-[-.02em] max-[520px]:text-[22px]">
				{value}
				{suffix ? (
					<small className="ml-1.5 text-[15px] font-semibold text-foreground-2 max-[520px]:m-0 max-[520px]:block max-[520px]:text-[12.5px]">
						{suffix}
					</small>
				) : null}
			</div>
			{children ? <div className="text-[12.5px] text-muted-foreground">{children}</div> : null}
		</div>
	);
}

/** "▲ 12% vs previous 7 days": arrow and colour are extra; the words are read out. */
export function Delta({ d, goodWhenUp = true, after }: { d: DeltaText | null; goodWhenUp?: boolean; after: string }) {
	if (!d) return null;
	const good = d.dir === "same" ? null : (d.dir === "up") === goodWhenUp;
	return (
		<>
			<b className={cn("font-semibold", good === true && "text-success", good === false && "text-destructive")}>
				<span aria-hidden>{d.short}</span>
				<span className="sr-only">{d.sr}</span>
			</b>{" "}
			{after}
		</>
	);
}

export interface KpiData {
	questions: { now: number; prev: number } | null;
	confidence: { now: number | null; prev: number | null } | null;
	tokens: { now: number; costNow: number; costPrev: number } | null;
	documents: { total: number; kbs: number; waitingReview: number | null } | null;
}

export function KpiCards({
	data,
	errors,
	vsPrevious,
	onRetry,
}: {
	data: KpiData;
	errors: { usage: unknown; trends: unknown; corpora: unknown };
	vsPrevious: string;
	onRetry: { usage: () => void; trends: () => void; corpora: () => void };
}) {
	const currency = useCurrency();
	const { questions, confidence, tokens, documents } = data;
	return (
		<div className="grid grid-cols-4 gap-4 max-[1100px]:grid-cols-2 max-[520px]:gap-2.5">
			{errors.usage ? (
				<ErrorState title={k.questions} error={errors.usage} onRetry={onRetry.usage} />
			) : (
				<KpiCard
					testId="kpi-questions"
					icon={MessageSquare}
					label={k.questions}
					value={questions ? formatNumber(questions.now) : "…"}
				>
					{questions ? (
						questions.prev > 0 ? (
							<Delta d={describeDelta(relativeDelta(questions.now, questions.prev), "pct")} after={vsPrevious} />
						) : (
							k.noPrevious
						)
					) : null}
				</KpiCard>
			)}
			{errors.trends ? (
				<ErrorState title={k.confidence} error={errors.trends} onRetry={onRetry.trends} />
			) : (
				<KpiCard
					testId="kpi-confidence"
					icon={BadgeCheck}
					label={k.confidence}
					value={!confidence ? "…" : confidence.now == null ? "—" : Math.round(confidence.now * 100)}
					suffix={confidence?.now != null ? k.confidenceAvg : undefined}
				>
					{confidence ? (
						confidence.now == null ? (
							k.confidenceNone
						) : (
							<Delta d={describeDelta(pointsDelta(confidence.now, confidence.prev), "pts")} after={vsPrevious} />
						)
					) : null}
				</KpiCard>
			)}
			{errors.usage ? (
				<ErrorState title={k.tokens} error={errors.usage} onRetry={onRetry.usage} />
			) : (
				<KpiCard
					testId="kpi-tokens"
					icon={Cpu}
					label={k.tokens}
					value={tokens ? formatCompact(tokens.now) : "…"}
					suffix={tokens ? `≈ ${formatMoney(tokens.costNow, currency)}` : undefined}
				>
					{tokens ? (
						<>
							{tokens.costPrev > 0 ? (
								<>
									<Delta
										d={describeDelta(relativeDelta(tokens.costNow, tokens.costPrev), "pct")}
										goodWhenUp={false}
										after={k.spend}
									/>
									{" · "}
								</>
							) : null}
							{k.tokensNote}
						</>
					) : null}
				</KpiCard>
			)}
			{errors.corpora ? (
				<ErrorState title={k.documents} error={errors.corpora} onRetry={onRetry.corpora} />
			) : (
				<KpiCard
					testId="kpi-documents"
					icon={FileText}
					label={k.documents}
					value={documents ? formatNumber(documents.total) : "…"}
				>
					{documents
						? [
								fill(k.documentsIn, { kbs: plural(overviewStrings.kbs, documents.kbs, formatNumber(documents.kbs)) }),
								documents.waitingReview ? fill(k.waitingReview, { n: formatNumber(documents.waitingReview) }) : null,
							]
								.filter(Boolean)
								.join(" · ")
						: null}
				</KpiCard>
			)}
		</div>
	);
}
