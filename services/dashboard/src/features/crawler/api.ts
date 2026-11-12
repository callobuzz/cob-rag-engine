import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { invalidateDocumentSet } from "@/features/documents/api";
import { scraper } from "@/lib/api/scraper";
import type { ApproveRequest, ScrapePageStatus, ScrapeRequest } from "@/lib/api/scraper-types";
import { isJobActive } from "./statuses";

/**
 * Crawler data (D8), from the scraper service. Query keys live here (the scraper is not the
 * engine, so they are not in `qk`). Polling: the list every 5 s while any job is still moving,
 * a job and its pages every 3 s while it is.
 */

export const JOBS_POLL_MS = 5000;
export const JOB_POLL_MS = 3000;
export const CRAWLS_PAGE_SIZE = 25;

export const crawlerKeys = {
	all: ["crawler"] as const,
	jobs: ["crawler", "jobs"] as const,
	job: (id: string) => ["crawler", "job", id] as const,
	pagesAll: (id: string) => ["crawler", "pages", id] as const,
	pages: (id: string, status?: ScrapePageStatus) => ["crawler", "pages", id, status ?? "all"] as const,
	page: (id: string, pageId: string) => ["crawler", "page", id, pageId] as const,
};

export const jobsQuery = queryOptions({
	queryKey: crawlerKeys.jobs,
	queryFn: () => scraper.listJobs(),
	refetchInterval: (q) => (!q.state.error && q.state.data?.some((j) => isJobActive(j)) ? JOBS_POLL_MS : false),
});

export function jobQuery(id: string) {
	return queryOptions({
		queryKey: crawlerKeys.job(id),
		queryFn: () => scraper.getJob(id),
		refetchInterval: (q) => (!q.state.error && isJobActive(q.state.data) ? JOB_POLL_MS : false),
	});
}

export function pagesQuery(id: string, status: ScrapePageStatus | undefined, active: boolean) {
	return queryOptions({
		queryKey: crawlerKeys.pages(id, status),
		queryFn: () => scraper.listPages(id, status),
		refetchInterval: active ? JOB_POLL_MS : false,
		placeholderData: (prev) => prev,
	});
}

export function pageQuery(id: string, pageId: string) {
	return queryOptions({
		queryKey: crawlerKeys.page(id, pageId),
		queryFn: () => scraper.getPage(id, pageId),
	});
}

export function useStartCrawl() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: ScrapeRequest) => scraper.submit(body),
		onSuccess: () => qc.invalidateQueries({ queryKey: crawlerKeys.jobs }),
	});
}

function refreshJob(qc: ReturnType<typeof useQueryClient>, id: string) {
	return Promise.all([
		qc.invalidateQueries({ queryKey: crawlerKeys.job(id) }),
		qc.invalidateQueries({ queryKey: crawlerKeys.pagesAll(id) }),
		qc.invalidateQueries({ queryKey: crawlerKeys.jobs }),
	]);
}

export function useApprove(id: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: ApproveRequest) => scraper.approve(id, body),
		onSuccess: () => refreshJob(qc, id),
	});
}

/** Deliver approved pages; the knowledge base's documents, counts and plan usage change. */
export function useDeliver(id: string, kb: string | null) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () => scraper.deliver(id),
		onSettled: () => Promise.all([refreshJob(qc, id), kb ? invalidateDocumentSet(qc, kb) : null]),
	});
}

export function useDeleteCrawl() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => scraper.deleteJob(id),
		onSuccess: (_data, id) => {
			qc.removeQueries({ queryKey: crawlerKeys.job(id) });
			qc.removeQueries({ queryKey: crawlerKeys.pagesAll(id) });
			return qc.invalidateQueries({ queryKey: crawlerKeys.jobs });
		},
	});
}
