/**
 * The crawler (scraper service) API, typed by hand: the scraper is a separate service and its API
 * is not in docs/openapi.json. Checked against services/scraper at dashboard D8 (2026-09-30):
 *
 * - `scraper/storage/models.py` — `JobStatus` (l. 10), `PageStatus` (l. 22),
 *   `ScrapeJob.to_dict` (l. 64), `ScrapedPage.to_dict` (l. 102)
 * - `scraper/api/schemas.py` — `ScrapeRequest` (l. 8), `ScrapeResponse` (l. 42), `ApproveRequest` (l. 49)
 * - `scraper/api/routes.py` — `POST /scrape` (l. 188), `GET /jobs` (l. 238), `GET /jobs/{id}`
 *   (l. 246, adds `page_counts`), `GET /jobs/{id}/pages?status=` (l. 256), `GET
 *   /jobs/{id}/pages/{page_id}` (l. 271, adds `content_file`), `POST /jobs/{id}/approve` (l. 289),
 *   `POST /jobs/{id}/deliver` (l. 305, `_delivery_summary` l. 116), `DELETE /jobs/{id}` (l. 328)
 * - `scraper/api/auth.py` — errors from the engine pass through as `{detail, code, ...extra}`
 */

export const JOB_STATUSES = [
	"pending",
	"crawling",
	"extracting",
	"verifying",
	"delivering",
	"completed",
	"failed",
] as const;
export type ScrapeJobStatus = (typeof JOB_STATUSES)[number];

export const PAGE_STATUSES = [
	"queued",
	"fetched",
	"extracted",
	"verified",
	"rejected",
	"approved",
	"delivered",
	"failed",
] as const;
export type ScrapePageStatus = (typeof PAGE_STATUSES)[number];

/** `ScrapeJob.to_dict()`. */
export interface ScrapeJob {
	id: string;
	/** Display root: the first seed URL, else the sitemap. */
	url: string;
	max_pages: number;
	status: ScrapeJobStatus;
	created_at: string;
	updated_at: string;
	pages_found: number;
	pages_done: number;
	error: string | null;
	/** "" for jobs created before corpus targeting (admin-only, cannot be delivered). */
	target_corpus_id: string;
	owner_tenant_id: string | null;
	review_mode: boolean;
	seed_urls: string[];
	sitemap_url: string | null;
}

/** `GET /jobs/{id}`: the job plus `{page status: count}`. */
export interface ScrapeJobDetail extends ScrapeJob {
	page_counts: Partial<Record<ScrapePageStatus, number>>;
}

/** `ScrapedPage.to_dict()`. */
export interface ScrapePage {
	id: string;
	job_id: string;
	url: string;
	status: ScrapePageStatus;
	title: string;
	word_count: number;
	content_md: string;
	content_html: string;
	rejection_reason: string | null;
	fetched_at: string | null;
	extracted_at: string | null;
	/** Why the last delivery failed; cleared once the engine accepts the page. */
	delivery_error: string | null;
}

/** `GET /jobs/{id}/pages/{page_id}`: the page plus its extracted Markdown when it is on disk. */
export interface ScrapePageDetail extends ScrapePage {
	content_file?: string;
}

/** `ScrapeRequest`. */
export interface ScrapeRequest {
	sitemap_url?: string | null;
	urls?: string[] | null;
	target_corpus_id: string;
	review_mode: boolean;
	/** 1–1000; the scraper's default is 50. */
	max_pages?: number | null;
}

export interface ScrapeResponse {
	job_id: string;
	status: ScrapeJobStatus;
}

/** `ApproveRequest`: all verified pages, or the named ones (only verified pages qualify). */
export type ApproveRequest = { approve_all_verified: true } | { page_ids: string[] };

export interface ApproveResponse {
	approved: number;
}

export interface DeliveryResult {
	page_id: string;
	success: boolean;
	error?: string;
}

export interface DeliverResponse {
	attempted: number;
	delivered: number;
	failed: number;
	results: DeliveryResult[];
}

export interface DeleteJobResponse {
	deleted: boolean;
}
