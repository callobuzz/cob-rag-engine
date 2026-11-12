import { useQuery } from "@tanstack/react-query";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Panel } from "@/features/kbs/ui";
import type { Corpus } from "@/lib/api/queries";
import { formatRelative } from "@/lib/format/date";
import { formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { recentQuestionsQuery } from "./api";
import { HeadLink, List, Muted, Row } from "./ui";

const t = overviewStrings.recent;

/**
 * Tenant: the five latest answered questions (`/analytics/queries/history?limit=5`). Replaces the
 * mockup's "Most-asked questions": the engine has no aggregate endpoint for that (AUDIT #17).
 */
export function RecentQuestionsCard({ kbNames }: { kbNames: Map<string, Corpus> }) {
	const q = useQuery(recentQuestionsQuery);
	const items = q.data?.items ?? [];
	return (
		<Panel
			title={t.title}
			sub={t.sub}
			aside={
				<HeadLink to="/insights" label={t.allLabel}>
					{t.all}
				</HeadLink>
			}
		>
			{q.isPending ? (
				<TableSkeleton rows={3} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
				</div>
			) : items.length === 0 ? (
				<Muted>{t.empty}</Muted>
			) : (
				<List label={t.title}>
					{items.map((h) => (
						<Row
							key={h.id}
							wrap
							title={`“${h.query_text ?? "—"}”`}
							sub={[
								h.corpus_id ? kbNames.get(h.corpus_id)?.name : null,
								formatRelative(h.created_at),
								h.confidence_score != null ? fill(t.confidence, { p: formatPercent(h.confidence_score) }) : null,
							]
								.filter(Boolean)
								.join(" · ")}
						/>
					))}
				</List>
			)}
		</Panel>
	);
}
