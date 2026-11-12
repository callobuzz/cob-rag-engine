import { z } from "zod";
import type { Status } from "@/components/patterns/StatusBadge";
import { PAGE_STATUSES, type ScrapeJob, type ScrapeJobStatus, type ScrapePageStatus } from "@/lib/api/scraper-types";
import { crawlerStrings as t } from "@/lib/strings/crawler";

/**
 * Crawl job and page statuses (D8): labels, badge tones and "still moving".
 *
 * The scraper leaves a review-mode job at `verifying` once the crawl is over ("ready for review")
 * and never sets `delivering`; a review-mode-off job goes `verifying` → `completed` in the
 * background. So a job is still moving while it is pending/crawling/extracting/delivering, or
 * verifying with review mode off.
 */

const MOVING: ReadonlySet<ScrapeJobStatus> = new Set(["pending", "crawling", "extracting", "delivering"]);

export function isJobActive(job: Pick<ScrapeJob, "status" | "review_mode"> | null | undefined): boolean {
	if (!job) return false;
	return MOVING.has(job.status) || (job.status === "verifying" && !job.review_mode);
}

export function jobStatusLabel(job: Pick<ScrapeJob, "status" | "review_mode">): string {
	if (job.status === "verifying" && !job.review_mode) return t.jobStatus.verifyingAuto;
	return t.jobStatus[job.status] ?? job.status;
}

/** StatusBadge tone key (icon + text, never colour alone). */
export function jobBadge(job: Pick<ScrapeJob, "status" | "review_mode">): Status {
	if (job.status === "completed") return "completed";
	if (job.status === "failed") return "failed";
	if (job.status === "verifying" && job.review_mode) return "pending";
	if (job.status === "pending") return "queued";
	return "running";
}

export function pageStatusLabel(status: ScrapePageStatus): string {
	return t.pageStatus[status] ?? status;
}

const PAGE_BADGE: Record<ScrapePageStatus, Status> = {
	queued: "queued",
	fetched: "running",
	extracted: "running",
	verified: "pending",
	rejected: "suspended",
	approved: "active",
	delivered: "completed",
	failed: "failed",
};

export function pageBadge(status: ScrapePageStatus): Status {
	return PAGE_BADGE[status] ?? "queued";
}

/** Page statuses in the order the filter chips show them. */
export const PAGE_FILTER_ORDER: readonly ScrapePageStatus[] = [
	"verified",
	"approved",
	"delivered",
	"rejected",
	"failed",
	"extracted",
	"fetched",
	"queued",
];

/** `/crawler?kb=&page=&all=` */
export const crawlerSearchSchema = z.object({
	kb: z.uuid().optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
	/** 1 = show the crawls of every knowledge base. */
	all: z.coerce.number().int().min(0).max(1).optional().catch(undefined),
});
export type CrawlerSearch = z.infer<typeof crawlerSearchSchema>;

/** `/crawler/$jobId?status=&pageId=` */
export const crawlerJobSearchSchema = z.object({
	status: z.enum(PAGE_STATUSES).optional().catch(undefined),
	pageId: z.string().max(64).optional().catch(undefined),
});
export type CrawlerJobSearch = z.infer<typeof crawlerJobSearchSchema>;

/** The site as people say it: host + path, no scheme or trailing slash. */
export function siteLabel(url: string): string {
	try {
		const u = new URL(url);
		const path = u.pathname === "/" ? "" : u.pathname.replace(/\/+$/, "");
		return `${u.host}${path}`;
	} catch {
		return url;
	}
}

/** Only http(s) addresses (the scraper refuses anything else, and private addresses too). */
export function isCrawlableUrl(value: string): boolean {
	try {
		const u = new URL(value);
		return (u.protocol === "http:" || u.protocol === "https:") && !!u.hostname;
	} catch {
		return false;
	}
}
