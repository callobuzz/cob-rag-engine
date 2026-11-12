import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, CheckCheck, Loader, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { EmptyState } from "@/components/patterns/EmptyState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { CardSkeleton, TableSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { toApiError } from "@/lib/api/errors";
import { corporaSwitcherQuery } from "@/lib/api/queries";
import type { ScrapeJobDetail, ScrapePage, ScrapePageStatus } from "@/lib/api/scraper-types";
import { formatDateTime, formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { crawlerStrings as t } from "@/lib/strings/crawler";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { crawlerKeys, jobQuery, pagesQuery, useApprove, useDeleteCrawl } from "./api";
import { CrawlerError, crawlerMessage } from "./CrawlerError";
import { DeliverBar } from "./DeliverBar";
import { JobStatusBadge } from "./JobsTable";
import { PageDrawer } from "./PageDrawer";
import { PagesTable } from "./PagesTable";
import { isJobActive, PAGE_FILTER_ORDER, pageStatusLabel, siteLabel } from "./statuses";

const route = getRouteApi("/_app/crawler/$jobId");

/** `/crawler/$jobId?status=&pageId=`: summary, deliver bar, status chips, pages, page drawer. */
export function JobDetailPage() {
	const { jobId } = route.useParams();
	const q = useQuery(jobQuery(jobId));
	if (q.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				<PageHeader title={t.job.title} crumbs={[{ label: t.job.crumb, to: "/crawler" }]} />
				<CardSkeleton lines={4} />
				<TableSkeleton rows={4} />
			</div>
		);
	}
	if (q.isError) {
		const e = toApiError(q.error);
		if (e.status === 404) {
			return (
				<>
					<PageHeader title={t.job.title} crumbs={[{ label: t.job.crumb, to: "/crawler" }]} />
					<EmptyState
						title={t.job.notFoundTitle}
						body={t.job.notFoundBody}
						primaryAction={
							<Link to="/crawler" className={buttonVariants({ variant: "default" })}>
								<ArrowLeft aria-hidden />
								{t.job.back}
							</Link>
						}
					/>
				</>
			);
		}
		return (
			<div className="grid gap-4">
				<PageHeader title={t.job.title} crumbs={[{ label: t.job.crumb, to: "/crawler" }]} />
				<CrawlerError error={q.error} onRetry={() => void q.refetch()} />
			</div>
		);
	}
	return <Job job={q.data} />;
}

function Job({ job }: { job: ScrapeJobDetail }) {
	const { status, pageId } = route.useSearch();
	const navigate = useNavigate();
	const kbs = useQuery(corporaSwitcherQuery);
	const kbName = kbs.data?.items.find((k) => k.id === job.target_corpus_id)?.name ?? t.list.unknownKb;
	const active = isJobActive(job);
	const qc = useQueryClient();
	const wasActive = useRef(active);
	useEffect(() => {
		// The crawl just finished: read the pages once more (polling stops with the job).
		if (wasActive.current && !active) {
			void qc.invalidateQueries({ queryKey: crawlerKeys.pagesAll(job.id) });
			void qc.invalidateQueries({ queryKey: crawlerKeys.jobs });
		}
		wasActive.current = active;
	}, [active, qc, job.id]);
	const pages = useQuery(pagesQuery(job.id, status, active));
	const approve = useApprove(job.id);
	const del = useDeleteCrawl();
	const [deleting, setDeleting] = useState(false);
	const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
	const selectAllId = useId();

	const rows = pages.data ?? [];
	const verifiedRows = rows.filter((p) => p.status === "verified");
	// Only verified pages can be approved; drop picks that moved on.
	const selected: ReadonlySet<string> = new Set([...picked].filter((id) => verifiedRows.some((p) => p.id === id)));
	const allPicked = verifiedRows.length > 0 && verifiedRows.every((p) => selected.has(p.id));
	const somePicked = selected.size > 0 && !allPicked;
	const openSummary = rows.find((p) => p.id === pageId) ?? null;

	const setSearch = (next: { status?: ScrapePageStatus; pageId?: string }) =>
		void navigate({
			to: "/crawler/$jobId",
			params: { jobId: job.id },
			search: { status, pageId, ...next },
			replace: true,
		});

	const toggle = (id: string, on: boolean) =>
		setPicked((prev) => {
			const next = new Set(prev);
			if (on) next.add(id);
			else next.delete(id);
			return next;
		});

	const approveSelected = () =>
		approve.mutate(
			{ page_ids: [...selected] },
			{
				onSuccess: (r) => {
					setPicked(new Set());
					toast.success(
						r.approved === 1 ? t.pages.approvedToastOne : fill(t.pages.approvedToast, { n: formatNumber(r.approved) }),
					);
				},
			},
		);

	const counts = job.page_counts;
	const total = Object.values(counts).reduce((a, n) => a + (n ?? 0), 0);
	const chips = PAGE_FILTER_ORDER.filter((s) => (counts[s] ?? 0) > 0 || s === status);

	const confirmDelete = () =>
		del.mutate(job.id, {
			onSuccess: () => {
				toast.success(t.deleteDialog.deleted, { description: siteLabel(job.url) });
				void navigate({ to: "/crawler", search: { kb: job.target_corpus_id || undefined } });
			},
		});

	return (
		<div className="grid gap-5">
			<PageHeader
				title={siteLabel(job.url)}
				description={fill(t.job.lead, { site: job.url, kb: job.target_corpus_id ? kbName : t.list.noKb })}
				crumbs={[
					{ label: t.job.crumb, to: "/crawler", search: { kb: job.target_corpus_id || undefined } },
					{ label: siteLabel(job.url) },
				]}
				actions={
					<Button variant="outline" onClick={() => setDeleting(true)}>
						<Trash2 aria-hidden />
						{t.job.delete}
					</Button>
				}
			/>

			<section aria-labelledby="crawl-summary" className="rounded-lg border border-border bg-card">
				<h2 id="crawl-summary" className="border-b border-border px-4 py-3 text-[15px] font-bold">
					{t.job.summary}
				</h2>
				<KeyValue
					rows={[
						{ label: t.job.status, value: <JobStatusBadge job={job} /> },
						{
							label: t.job.kb,
							value: job.target_corpus_id ? (
								<Link to="/documents" search={{ kb: job.target_corpus_id }}>
									{kbName}
								</Link>
							) : (
								t.list.noKb
							),
						},
						{
							label: t.job.pages,
							value: fill(t.job.pagesValue, {
								done: formatNumber(job.pages_done),
								found: formatNumber(job.pages_found),
							}),
						},
						{ label: t.job.limit, value: formatNumber(job.max_pages) },
						{ label: t.job.review, value: job.review_mode ? t.job.reviewOn : t.job.reviewOff },
						{
							label: t.job.sources,
							value: (
								<ul className="m-0 grid list-none gap-0.5 p-0">
									{job.seed_urls.map((u) => (
										<li key={u}>{u}</li>
									))}
									{job.sitemap_url ? (
										<li>
											{job.sitemap_url} ({t.job.sitemap})
										</li>
									) : null}
									{!job.seed_urls.length && !job.sitemap_url ? <li>{job.url}</li> : null}
								</ul>
							),
						},
						{
							label: t.job.started,
							value: <span title={formatDateTime(job.created_at)}>{formatRelative(job.created_at)}</span>,
						},
						{
							label: t.job.updated,
							value: <span title={formatDateTime(job.updated_at)}>{formatRelative(job.updated_at)}</span>,
						},
						...(job.error
							? [{ label: t.job.error, value: <span className="text-destructive">{job.error}</span> }]
							: []),
					]}
				/>
			</section>

			{active ? (
				<p role="status" className="m-0 flex items-center gap-2 text-[13px] text-foreground-2">
					<Loader className="size-4 motion-safe:animate-spin" aria-hidden />
					{fill(t.job.crawlingNote, { site: siteLabel(job.url) })}
				</p>
			) : null}

			<DeliverBar job={job} kbName={job.target_corpus_id ? kbName : t.list.noKb} />

			<section aria-label={t.pages.caption} className="grid gap-3">
				{/* biome-ignore lint/a11y/useSemanticElements: a row of toggle chips; a fieldset would add a legend and border */}
				<div role="group" aria-label={t.pages.filter} className="flex flex-wrap gap-2" data-testid="status-chips">
					<Chip on={!status} onClick={() => setSearch({ status: undefined })} label={t.pages.all} count={total} />
					{chips.map((s) => (
						<Chip
							key={s}
							on={status === s}
							onClick={() => setSearch({ status: s })}
							label={pageStatusLabel(s)}
							count={counts[s] ?? 0}
						/>
					))}
				</div>
				{verifiedRows.length > 0 ? (
					<div className="flex flex-wrap items-center justify-between gap-3">
						<div className="flex items-center gap-2.5">
							<Checkbox
								id={selectAllId}
								checked={allPicked ? true : somePicked ? "indeterminate" : false}
								onCheckedChange={(v) => setPicked(v === true ? new Set(verifiedRows.map((p) => p.id)) : new Set())}
							/>
							<label htmlFor={selectAllId} className="text-[13px] font-semibold">
								{t.pages.selectAll}
							</label>
						</div>
						<Button variant="outline" disabled={selected.size === 0 || approve.isPending} onClick={approveSelected}>
							<CheckCheck aria-hidden />
							{fill(t.pages.approveSelected, { n: formatNumber(selected.size) })}
						</Button>
					</div>
				) : null}
				{approve.error ? (
					<p role="alert" className="m-0 text-[13px] text-destructive">
						{crawlerMessage(approve.error).title}
					</p>
				) : null}
				{pages.isPending ? (
					<div className="rounded-lg border border-border bg-card">
						<TableSkeleton rows={4} />
					</div>
				) : pages.isError ? (
					<CrawlerError error={pages.error} onRetry={() => void pages.refetch()} />
				) : (
					<div className="overflow-x-auto rounded-lg border border-border bg-card">
						<PagesTable
							pages={rows}
							selected={selected}
							onToggle={toggle}
							onOpen={(p: ScrapePage) => setSearch({ pageId: p.id })}
							empty={status ? t.pages.emptyFiltered : t.pages.empty}
						/>
					</div>
				)}
			</section>

			<PageDrawer
				jobId={job.id}
				pageId={pageId ?? null}
				summary={openSummary}
				onClose={() => setSearch({ pageId: undefined })}
			/>
			<ConfirmDialog
				open={deleting}
				onOpenChange={(o) => {
					setDeleting(o);
					if (!o) del.reset();
				}}
				title={t.deleteDialog.title}
				body={t.deleteDialog.body}
				confirmLabel={t.deleteDialog.confirm}
				pending={del.isPending}
				onConfirm={confirmDelete}
			>
				{del.error ? (
					<p role="alert" className="m-0 text-[13px] font-medium text-destructive">
						{crawlerMessage(del.error).title}
					</p>
				) : null}
			</ConfirmDialog>
		</div>
	);
}

function Chip({ on, onClick, label, count }: { on: boolean; onClick: () => void; label: string; count: number }) {
	return (
		<button
			type="button"
			aria-pressed={on}
			onClick={onClick}
			className={cn(
				"inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[12.5px] font-semibold",
				on ? "border-primary bg-primary-soft text-primary" : "border-border bg-card text-foreground-2 hover:bg-accent",
			)}
		>
			{label} <span className="font-mono tabular-nums">{formatNumber(count)}</span>
		</button>
	);
}
