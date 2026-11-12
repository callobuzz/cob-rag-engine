import { useQuery } from "@tanstack/react-query";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Panel } from "@/features/kbs/ui";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { fairUseOwnerStrings as t } from "@/lib/strings/pipelines";
import { fairUseMineQuery } from "./api";
import { OwnerMetricMeter } from "./FairUseMeters";

/**
 * Plan & billing: "Answer speed" with one sentence for what it means, and each limit that matters
 * as a meter. Owners and members read the same; AI usage is a share ("82%"), never an amount.
 */
export function OwnerFairUseCard() {
	const q = useQuery(fairUseMineQuery);
	const { productName } = useBranding();
	return (
		<Panel title={t.card.title} sub={t.card.lead}>
			{q.isPending ? (
				<CardSkeleton lines={3} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.card.loadError} onRetry={() => void q.refetch()} />
				</div>
			) : (
				<div className="grid gap-4 p-4" data-testid="owner-fair-use">
					<div className="grid gap-0.5 text-[13.5px]">
						<b data-testid="owner-answer-speed">{fill(t.card.speed, { label: q.data.answer_speed })}</b>
						<span className="text-foreground-2">{t.card.speedLine[q.data.answer_speed] ?? t.card.speedOther}</span>
					</div>
					{q.data.answers_paused ? (
						<p
							data-testid="owner-fair-use-local"
							role="alert"
							className="m-0 text-[13px] font-semibold text-destructive"
						>
							{fill(t.localNotReady, { product: productName })}
						</p>
					) : null}
					<p data-testid="owner-fair-use-state" className="m-0 text-[13px] text-foreground-2">
						{t.card.state[q.data.state]}
					</p>
					{q.data.metrics.length === 0 ? (
						<p className="m-0 text-[13px] text-muted-foreground">{t.card.none}</p>
					) : (
						q.data.metrics.map((m) => <OwnerMetricMeter key={m.metric} row={m} />)
					)}
				</div>
			)}
		</Panel>
	);
}
