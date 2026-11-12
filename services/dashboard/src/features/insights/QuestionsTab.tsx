import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { MessageSquare } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/features/kbs/ui";
import { formatDateTime, formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import { HISTORY_PAGE_SIZE, type HistoryItem, historyQuery } from "./api";
import { ConfidenceBadge } from "./confidence";
import type { ScopeProps } from "./InsightsPage";
import { QuestionDrawer } from "./QuestionDrawer";
import { FilterRow, useKbNames } from "./ui";

const t = insightsStrings.questions;

export function formatLatency(ms: number | null | undefined): string {
	if (ms == null) return "—";
	return ms < 1000 ? `${formatNumber(Math.round(ms))} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** Questions tab: `GET /analytics/queries/history` with paging and a session filter; a drawer per question. */
export function QuestionsTab({ kb, corpusId, search, setSearch }: ScopeProps) {
	const page = search.page ?? 1;
	const q = useQuery(historyQuery({ corpusId, page, session: search.session }));
	const kbName = useKbNames();
	const [session, setSession] = useState(search.session ?? "");
	const ids = { filter: useId(), session: useId() };
	const filter = (search.q ?? "").trim().toLocaleLowerCase("en-IN");

	const onSession = (e: FormEvent) => {
		e.preventDefault();
		setSearch({ session: session.trim() || undefined, page: undefined });
	};

	const rows = (q.data?.items ?? []).filter(
		(r) => !filter || (r.query_text ?? "").toLocaleLowerCase("en-IN").includes(filter),
	);
	const noneAtAll = q.data && q.data.total === 0 && !search.session;

	const columns: Column<HistoryItem>[] = [
		{
			id: "question",
			header: t.colQuestion,
			rowHeader: true,
			className: "min-w-[240px]",
			cell: (r) => (
				<button
					type="button"
					className="text-left font-semibold text-foreground underline-offset-4 hover:underline"
					aria-label={fill(t.open, { q: r.query_text ?? t.noQuestionText })}
					onClick={() => setSearch({ question: r.id })}
				>
					{r.query_text || t.noQuestionText}
				</button>
			),
		},
		{
			id: "confidence",
			header: t.colConfidence,
			stackLabel: true,
			cell: (r) => <ConfidenceBadge score={r.confidence_score} />,
			sortValue: (r) => r.confidence_score ?? -1,
		},
		{
			id: "latency",
			header: t.colLatency,
			align: "right",
			stackLabel: true,
			cell: (r) => formatLatency(r.latency_ms),
			sortValue: (r) => r.latency_ms ?? -1,
		},
		...(corpusId
			? []
			: [{ id: "kb", header: t.colKb, stackLabel: true, cell: (r: HistoryItem) => kbName(r.corpus_id) }]),
		{
			id: "time",
			header: t.colTime,
			stackLabel: true,
			cell: (r) => <time title={formatDateTime(r.created_at)}>{formatRelative(r.created_at)}</time>,
		},
	];

	if (noneAtAll) {
		return (
			<>
				<EmptyState
					title={t.emptyTitle}
					body={t.emptyBody}
					primaryAction={
						<Link to="/playground" search={{ kb: kb.id }} className={buttonVariants({ variant: "default" })}>
							<MessageSquare aria-hidden />
							{t.openPlayground}
						</Link>
					}
				/>
				<QuestionDrawer
					id={search.question}
					kb={kb}
					row={q.data?.items.find((r) => r.id === search.question)}
					onClose={() => setSearch({ question: undefined })}
				/>
			</>
		);
	}

	return (
		<Panel title={t.title} sub={q.data ? fill(t.sub, { n: formatNumber(q.data.total) }) : undefined}>
			<FilterRow className="border-b border-border">
				<div className="grid gap-1.5">
					<label htmlFor={ids.filter} className="text-[13px] font-semibold">
						{t.filter}
					</label>
					<Input
						id={ids.filter}
						type="search"
						className="w-[240px] max-w-full"
						placeholder={t.filterPlaceholder}
						value={search.q ?? ""}
						onChange={(e) => setSearch({ q: e.target.value || undefined })}
					/>
				</div>
				<form onSubmit={onSession} className="flex flex-wrap items-end gap-2">
					<div className="grid gap-1.5">
						<label htmlFor={ids.session} className="text-[13px] font-semibold">
							{t.session}
						</label>
						<Input
							id={ids.session}
							className="w-[240px] max-w-full font-mono"
							placeholder={t.sessionPlaceholder}
							value={session}
							onChange={(e) => setSession(e.target.value)}
						/>
					</div>
					<Button type="submit">{t.apply}</Button>
					{search.session ? (
						<Button
							type="button"
							variant="ghost"
							onClick={() => {
								setSession("");
								setSearch({ session: undefined, page: undefined });
							}}
						>
							{t.clear}
						</Button>
					) : null}
				</form>
			</FilterRow>
			{q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
				</div>
			) : q.isPending ? (
				<div className="p-4">
					<TableSkeleton rows={6} />
				</div>
			) : (
				<>
					<div className="px-2">
						<DataTable
							columns={columns}
							rows={rows}
							rowKey={(r) => r.id}
							caption={t.title}
							empty={filter ? t.emptyFiltered : t.empty}
						/>
					</div>
					{q.data.total > HISTORY_PAGE_SIZE ? (
						<Pager
							page={page}
							pageSize={HISTORY_PAGE_SIZE}
							total={q.data.total}
							onPage={(p) => setSearch({ page: p })}
						/>
					) : null}
				</>
			)}
			<QuestionDrawer
				id={search.question}
				kb={kb}
				row={q.data?.items.find((r) => r.id === search.question)}
				onClose={() => setSearch({ question: undefined })}
			/>
		</Panel>
	);
}
