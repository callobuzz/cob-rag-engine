import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Star, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Pager } from "@/components/patterns/Pager";
import { Segmented } from "@/components/patterns/Segmented";
import { CardSkeleton, TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Kpi, Panel } from "@/features/kbs/ui";
import { useGoldenFromFeedback } from "@/features/quality/api";
import { formatDateTime, formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import {
	FEEDBACK_PAGE_SIZE,
	type FeedbackItem,
	feedbackListQuery,
	feedbackStatsQuery,
	RANGES,
	type Range,
	rangeStart,
} from "./api";
import type { ScopeProps } from "./InsightsPage";
import { FilterRow, Note, useErrorText, useKbNames } from "./ui";

const t = insightsStrings.feedback;

/** 👍 (≥ 4), 👎 (≤ 2) or "n of 5" — icon + text, never colour alone. */
export function FeedbackRating({ rating }: { rating: number | null }) {
	if (rating == null) return <Badge>—</Badge>;
	if (rating >= 4)
		return (
			<Badge variant="ok">
				<ThumbsUp aria-hidden />
				{t.ratings.up}
			</Badge>
		);
	if (rating <= 2)
		return (
			<Badge variant="bad">
				<ThumbsDown aria-hidden />
				{t.ratings.down}
			</Badge>
		);
	return <Badge>{fill(t.stars, { n: rating })}</Badge>;
}

/**
 * Feedback tab: totals (`GET /feedback/stats`, one knowledge base only), the list (`GET /feedback`
 * with rating and period filters, paging) and "Add to golden set" (`POST /golden/from-feedback`,
 * only for ratings with a correction — the engine refuses the others).
 */
export function FeedbackTab({ corpusId, search, setSearch }: ScopeProps) {
	const [range, setRange] = useState<Range>("30d");
	const start = rangeStart(range);
	const page = search.page ?? 1;
	const q = useQuery(feedbackListQuery({ corpusId, rating: search.rating, start, page }));
	const kbName = useKbNames();
	const promote = useGoldenFromFeedback();
	const errorText = useErrorText();
	const navigate = useNavigate();

	const addGolden = (f: FeedbackItem) =>
		promote.mutate(f.id, {
			onSuccess: (pair) =>
				toast.success(t.added, {
					description: fill(t.addedBody, { q: pair.query }),
					action: {
						label: t.viewGolden,
						onClick: () => void navigate({ to: "/quality", search: { tab: "golden", kb: pair.corpus_id } }),
					},
				}),
			onError: (e) => toast.error(errorText(e)),
		});

	const columns: Column<FeedbackItem>[] = [
		{
			id: "question",
			header: t.colQuestion,
			rowHeader: true,
			className: "min-w-[220px]",
			cell: (f) => (
				<div className="grid gap-0.5">
					<span className="font-semibold">{f.question ?? "—"}</span>
					{corpusId ? null : <span className="text-[12px] text-muted-foreground">{kbName(f.corpus_id)}</span>}
				</div>
			),
		},
		{ id: "rating", header: t.colRating, stackLabel: true, cell: (f) => <FeedbackRating rating={f.rating} /> },
		{
			id: "correction",
			header: t.colCorrection,
			stackLabel: true,
			className: "min-w-[200px]",
			cell: (f) => (f.correction ? <span className="whitespace-pre-wrap">{f.correction}</span> : "—"),
		},
		{
			id: "type",
			header: t.colType,
			stackLabel: true,
			cell: (f) => (t.types as Record<string, string>)[f.feedback_type] ?? f.feedback_type,
		},
		{
			id: "time",
			header: t.colTime,
			stackLabel: true,
			cell: (f) => <time title={formatDateTime(f.created_at)}>{formatRelative(f.created_at)}</time>,
		},
		{
			id: "golden",
			header: t.addGolden,
			cell: (f) =>
				f.correction && f.query_id ? (
					<Button
						size="sm"
						aria-label={fill(t.addGoldenFor, { q: f.question ?? "" })}
						disabled={promote.isPending && promote.variables === f.id}
						onClick={() => addGolden(f)}
					>
						<Star aria-hidden />
						{t.addGolden}
					</Button>
				) : (
					<small className="text-[12px] text-muted-foreground">{t.needsCorrection}</small>
				),
		},
	];

	return (
		<>
			<FilterRow className="px-0 py-0">
				<Segmented
					label={t.rating}
					value={search.rating ?? "all"}
					options={(["all", "up", "down"] as const).map((v) => ({ value: v, label: t.ratings[v] }))}
					onChange={(v) => setSearch({ rating: v === "all" ? undefined : v, page: undefined })}
				/>
				<Segmented
					label={insightsStrings.rangeLabel}
					value={range}
					options={RANGES.map((r) => ({ value: r, label: insightsStrings.ranges[r] }))}
					onChange={(r) => {
						setRange(r);
						setSearch({ page: undefined });
					}}
				/>
			</FilterRow>
			{corpusId ? <FeedbackStatsCards corpusId={corpusId} start={start} range={range} /> : <Note>{t.statsNeedKb}</Note>}
			<Panel title={t.title} sub={t.sub}>
				{q.isError ? (
					<div className="p-4">
						<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
					</div>
				) : q.isPending ? (
					<div className="p-4">
						<TableSkeleton rows={5} />
					</div>
				) : (
					<>
						<div className="px-2">
							<DataTable columns={columns} rows={q.data.items} rowKey={(f) => f.id} caption={t.title} empty={t.empty} />
						</div>
						{q.data.total > FEEDBACK_PAGE_SIZE ? (
							<Pager
								page={page}
								pageSize={FEEDBACK_PAGE_SIZE}
								total={q.data.total}
								onPage={(p) => setSearch({ page: p })}
							/>
						) : null}
					</>
				)}
			</Panel>
		</>
	);
}

function FeedbackStatsCards({ corpusId, start, range }: { corpusId: string; start?: string; range: Range }) {
	const q = useQuery(feedbackStatsQuery({ corpusId, start }));
	if (q.isError) return <ErrorState error={q.error} title={t.statsError} onRetry={() => void q.refetch()} />;
	if (q.isPending) return <CardSkeleton lines={1} />;
	const s = q.data;
	// Without a start the engine counts the last 30 days.
	const period = range === "30d" || range === "all" ? t.stats.period : t.stats.periodRange;
	return (
		<div className="grid grid-cols-2 gap-4 min-[861px]:grid-cols-5" data-testid="feedback-stats">
			<Kpi label={t.stats.total} value={formatNumber(s.total)} delta={period} />
			<Kpi label={t.stats.positive} value={formatNumber(s.positive)} />
			<Kpi label={t.stats.negative} value={formatNumber(s.negative)} />
			<Kpi label={t.stats.corrections} value={formatNumber(s.corrections)} />
			<Kpi label={t.stats.implicit} value={formatNumber(s.implicit)} />
		</div>
	);
}
