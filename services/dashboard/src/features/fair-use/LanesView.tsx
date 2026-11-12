import { useQuery } from "@tanstack/react-query";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Panel } from "@/features/kbs/ui";
import { lanesQuery, lanesRefetchInterval } from "@/features/pipelines/api";
import { fill } from "@/lib/strings/en";
import { lanesStrings as t } from "@/lib/strings/pipelines";

/**
 * "Answers right now": how many answers each pool is writing and who is waiting, read every
 * 5 seconds while the page is visible (the poll stops when the tab is hidden).
 */
export function LanesView() {
	const q = useQuery({ ...lanesQuery, refetchInterval: () => lanesRefetchInterval() });
	return (
		<Panel title={t.title} sub={t.lead}>
			{q.isPending ? (
				<CardSkeleton lines={2} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
				</div>
			) : !q.data.enabled ? (
				<div className="grid gap-1 px-4 py-4 text-[13.5px]" data-testid="lanes-off">
					<b>{t.off}</b>
					<span className="text-foreground-2">{t.offBody}</span>
				</div>
			) : (
				<div className="grid gap-3 p-4" data-testid="lanes-pools">
					{Object.entries(q.data.pools).map(([name, pool]) => {
						const by = pool.by_priority;
						return (
							<div key={name} className="grid gap-1 rounded-md border border-border p-3" data-pool={name}>
								<div className="flex flex-wrap items-center justify-between gap-2">
									<b className="text-[13.5px]">{t.pools[name] ?? name}</b>
									<Badge variant={pool.slots > 0 && pool.busy >= pool.slots ? "warn" : "ok"}>
										{fill(t.busy, { busy: pool.busy, slots: pool.slots })}
									</Badge>
								</div>
								<span className="text-[12.5px] text-foreground-2">
									{pool.busy === 0
										? t.idle
										: fill(t.by, { high: by.high ?? 0, normal: by.normal ?? 0, low: by.low ?? 0 })}
								</span>
							</div>
						);
					})}
					<small className="text-[12px] text-muted-foreground">{t.updated}</small>
				</div>
			)}
		</Panel>
	);
}
