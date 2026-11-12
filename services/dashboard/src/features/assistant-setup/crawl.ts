import { ApiError } from "@/lib/api/errors";
import { scraper } from "@/lib/api/scraper";

/**
 * Crawl the owner's website through the scraper service (review mode: nothing is delivered unless
 * the owner asks) and return the page text for the engine's proposal step. The engine never calls
 * the scraper; the dashboard does, with the signed-in person's own credential.
 *
 * Page text is untrusted site content going to a model: it is only counted and its titles shown,
 * never rendered. Each page is cut to 4,000 characters (the home, about, contact and price pages
 * carry what the proposal needs); the engine caps the total (`RAG_SETUP_MAX_CHARS`).
 */
export const PAGE_CHARS = 4000;
export const CRAWL_DEADLINE_MS = 3 * 60_000;
const TERMINAL = new Set(["verifying", "completed", "failed"]);

/** A test seam: the poll interval between job checks (tests set it to 0). */
export const crawlSettings = { pollMs: 1500, deadlineMs: CRAWL_DEADLINE_MS };

export class CrawlTimeoutError extends Error {
	constructor() {
		super("The crawl took too long");
		this.name = "CrawlTimeoutError";
	}
}

export interface CrawledPage {
	url: string;
	title: string;
	text: string;
}

export interface CrawlInput {
	url: string;
	kb: string;
	maxPages: number;
	onProgress: (p: { found: number; done: number }) => void;
	signal?: AbortSignal;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
	return new Promise((resolve, reject) => {
		if (signal?.aborted) return reject(new DOMException("aborted", "AbortError"));
		const t = setTimeout(resolve, ms);
		signal?.addEventListener(
			"abort",
			() => {
				clearTimeout(t);
				reject(new DOMException("aborted", "AbortError"));
			},
			{ once: true },
		);
	});
}

export async function crawlSite(input: CrawlInput): Promise<{ jobId: string; pages: CrawledPage[] }> {
	const { job_id } = await scraper.submit({
		urls: [input.url],
		review_mode: true,
		max_pages: input.maxPages,
		target_corpus_id: input.kb,
	});
	const started = Date.now();
	for (;;) {
		if (input.signal?.aborted) throw new DOMException("aborted", "AbortError");
		const job = await scraper.getJob(job_id);
		input.onProgress({ found: job.pages_found, done: job.pages_done });
		if (job.status === "failed") {
			throw new ApiError({ status: 422, code: null, detail: job.error ?? "The crawl failed" });
		}
		if (TERMINAL.has(job.status)) break;
		if (Date.now() - started > crawlSettings.deadlineMs) throw new CrawlTimeoutError();
		await sleep(crawlSettings.pollMs, input.signal);
	}
	const listed = (await scraper.listPages(job_id, "verified")).slice(0, input.maxPages);
	// The list already carries the extracted Markdown; fetch a page only when it came back empty.
	const pages = await Promise.all(
		listed.map(async (p) => {
			const md = p.content_md || (await scraper.getPage(job_id, p.id)).content_file || "";
			return { url: p.url, title: p.title, text: md.slice(0, PAGE_CHARS) };
		}),
	);
	return { jobId: job_id, pages: pages.filter((p) => p.text.trim().length > 0) };
}

/** Throw the pages of a setup crawl away (they were only read for the proposal). */
export async function discardCrawl(jobId: string): Promise<void> {
	try {
		await scraper.deleteJob(jobId);
	} catch {
		// Housekeeping only: a leftover review job is visible and deletable in the Crawler screen.
	}
}

/** Add the crawled pages to the knowledge base through the crawler's own approve and deliver steps. */
export async function deliverCrawl(jobId: string): Promise<{ delivered: number; failed: number }> {
	await scraper.approve(jobId, { approve_all_verified: true });
	const out = await scraper.deliver(jobId);
	return { delivered: out.delivered, failed: out.failed };
}
