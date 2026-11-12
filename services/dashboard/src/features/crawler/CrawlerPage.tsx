import { useQuery } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { Globe, Plus } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { EmptyState } from "@/components/patterns/EmptyState";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { type Corpus, corporaSwitcherQuery } from "@/lib/api/queries";
import type { ScrapeJob } from "@/lib/api/scraper-types";
import { crawlerStrings as t } from "@/lib/strings/crawler";
import { fill } from "@/lib/strings/en";
import { CRAWLS_PAGE_SIZE, jobsQuery, useDeleteCrawl } from "./api";
import { CrawlerError, crawlerMessage } from "./CrawlerError";
import { JobsTable } from "./JobsTable";
import { NewCrawlDialog } from "./NewCrawlDialog";
import { siteLabel } from "./statuses";

const route = getRouteApi("/_app/crawler/");

/** `/crawler?kb=&page=&all=` — crawls of the selected knowledge base (or all of them). */
export function CrawlerPage() {
	return (
		<KbScopedPage title={t.list.title} emptyBody={t.list.emptyBody}>
			{(kb) => <Crawler key={kb.id} kb={kb} />}
		</KbScopedPage>
	);
}

function Crawler({ kb }: { kb: Corpus }) {
	const { page = 1, all } = route.useSearch();
	const showAll = all === 1;
	const navigate = useNavigate();
	const allId = useId();
	const jobs = useQuery(jobsQuery);
	const kbs = useQuery(corporaSwitcherQuery);
	const [creating, setCreating] = useState(false);
	const [deleting, setDeleting] = useState<ScrapeJob | null>(null);
	const del = useDeleteCrawl();

	const names = useMemo(() => new Map((kbs.data?.items ?? []).map((k) => [k.id, k.name])), [kbs.data]);
	const kbName = (id: string) => names.get(id) ?? null;
	const everything = jobs.data ?? [];
	const mine = everything.filter((j) => j.target_corpus_id === kb.id);
	const rows = showAll ? everything : mine;
	const pageRows = rows.slice((page - 1) * CRAWLS_PAGE_SIZE, page * CRAWLS_PAGE_SIZE);

	const setSearch = (next: { page?: number; all?: number }) =>
		void navigate({
			to: "/crawler",
			search: { kb: kb.id, page: page > 1 ? page : undefined, all: showAll ? 1 : undefined, ...next },
			replace: true,
		});

	const confirmDelete = () => {
		if (!deleting) return;
		del.mutate(deleting.id, {
			onSuccess: () => {
				toast.success(t.deleteDialog.deleted, { description: siteLabel(deleting.url) });
				setDeleting(null);
			},
		});
	};

	return (
		<div className="grid gap-5">
			<PageHeader
				title={t.list.title}
				description={fill(t.list.lead, { kb: kb.name })}
				actions={
					<Button variant="default" onClick={() => setCreating(true)}>
						<Plus aria-hidden />
						{t.list.newCrawl}
					</Button>
				}
			/>
			{jobs.isSuccess && (everything.length > 0 || showAll) ? (
				<div className="flex items-center gap-2.5">
					<Switch
						id={allId}
						checked={showAll}
						onCheckedChange={(on) => setSearch({ all: on ? 1 : undefined, page: undefined })}
					/>
					<label htmlFor={allId} className="text-[13px] font-semibold">
						{t.list.allKbs}
					</label>
				</div>
			) : null}
			{jobs.isPending ? (
				<div className="rounded-lg border border-border bg-card">
					<TableSkeleton rows={3} />
				</div>
			) : jobs.isError ? (
				<CrawlerError error={jobs.error} onRetry={() => void jobs.refetch()} />
			) : rows.length === 0 ? (
				<EmptyState
					title={fill(t.list.emptyTitle, { kb: kb.name })}
					body={
						<>
							{t.list.emptyBody}
							{!showAll && everything.length > 0 ? (
								<span className="mt-2 block">
									{fill(t.list.emptyOtherKb, { kb: kb.name, n: everything.length })}{" "}
									<Button variant="link" className="h-auto p-0" onClick={() => setSearch({ all: 1, page: undefined })}>
										{t.list.showAll}
									</Button>
								</span>
							) : null}
						</>
					}
					primaryAction={
						<Button variant="default" onClick={() => setCreating(true)}>
							<Globe aria-hidden />
							{t.list.newCrawl}
						</Button>
					}
					art={
						<ul className="m-0 grid list-none gap-2.5 p-0">
							{t.list.emptyWays.map((w) => (
								<li key={w.b} className="grid gap-0.5 rounded-md border border-border bg-muted px-4 py-3">
									<b className="text-[13.5px]">{w.b}</b>
									<span className="text-[12.5px] text-foreground-2">{w.t}</span>
								</li>
							))}
						</ul>
					}
				/>
			) : (
				<div className="overflow-x-auto rounded-lg border border-border bg-card">
					<JobsTable jobs={pageRows} kbName={kbName} showKb={showAll} onDelete={setDeleting} />
					{rows.length > CRAWLS_PAGE_SIZE ? (
						<Pager
							page={page}
							pageSize={CRAWLS_PAGE_SIZE}
							total={rows.length}
							onPage={(p) => setSearch({ page: p > 1 ? p : undefined })}
						/>
					) : null}
				</div>
			)}
			<NewCrawlDialog open={creating} onOpenChange={setCreating} defaultKb={kb.id} />
			<ConfirmDialog
				open={!!deleting}
				onOpenChange={(o) => {
					if (!o) {
						setDeleting(null);
						del.reset();
					}
				}}
				title={t.deleteDialog.title}
				body={t.deleteDialog.body}
				confirmLabel={t.deleteDialog.confirm}
				pending={del.isPending}
				onConfirm={confirmDelete}
			>
				{del.error ? <CrawlerErrorLine error={del.error} /> : null}
			</ConfirmDialog>
		</div>
	);
}

function CrawlerErrorLine({ error }: { error: unknown }) {
	return (
		<p role="alert" className="m-0 text-[13px] font-medium text-destructive">
			{crawlerMessage(error).title}
		</p>
	);
}
