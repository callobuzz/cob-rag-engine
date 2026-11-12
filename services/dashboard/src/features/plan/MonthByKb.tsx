import { useQueries, useQuery } from "@tanstack/react-query";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { KbAvatar } from "@/features/kbs/KbAvatar";
import { Panel } from "@/features/kbs/ui";
import { List, Muted, Row } from "@/features/overview/ui";
import { usageQuery } from "@/features/usage/api";
import type { Corpus } from "@/lib/api/queries";
import { formatNumber, formatPercent } from "@/lib/format/number";
import { monthStartUtc, utcDate } from "@/lib/format/range";
import { fill } from "@/lib/strings/en";
import { planPageStrings } from "@/lib/strings/overview";
import { sourceSplit } from "./api";

const t = planPageStrings.byKb;
const KIND: Record<string, string> = t.kinds;

/** "September 2026" for a YYYY-MM-DD (UTC). */
function monthName(day: string): string {
	return new Date(`${day}T00:00:00Z`).toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });
}

/**
 * Questions this UTC month per knowledge base (`/usage?group_by=corpus` from the 1st, the same
 * month the plan counts), with each one's split by source.
 */
export function MonthByKb({ corpora }: { corpora: Corpus[] }) {
	const start = monthStartUtc();
	const end = utcDate(Date.now());
	const usage = useQuery(usageQuery({ group_by: "corpus", start, end }));
	const splits = useQueries({
		queries: corpora.map((c) => usageQuery({ group_by: "source", corpus_id: c.id, start, end })),
	});
	const by = new Map((usage.data?.rows ?? []).map((r) => [r.key ?? "", r.requests]));
	const rows = corpora
		.map((c, i) => ({ c, n: by.get(c.id) ?? 0, split: sourceSplit(splits[i]?.data?.rows ?? []) }))
		.filter((x) => x.n > 0)
		.sort((a, b) => b.n - a.n);
	return (
		<Panel title={t.title} sub={fill(t.sub, { month: monthName(start) })}>
			{usage.isPending ? (
				<TableSkeleton rows={2} />
			) : usage.isError ? (
				<div className="p-4">
					<ErrorState error={usage.error} title={t.error} onRetry={() => void usage.refetch()} />
				</div>
			) : rows.length === 0 ? (
				<Muted>{t.none}</Muted>
			) : (
				<List label={t.title}>
					{rows.map(({ c, n, split }) => (
						<Row
							key={c.id}
							testId="month-kb"
							lead={<KbAvatar kb={c} size={28} />}
							title={c.name}
							sub={
								split.length
									? split.map((s) => `${KIND[s.kind] ?? s.kind} ${formatPercent(s.share)}`).join(" · ")
									: undefined
							}
							trail={<b className="font-mono font-medium tabular-nums">{formatNumber(n)}</b>}
						/>
					))}
				</List>
			)}
		</Panel>
	);
}
