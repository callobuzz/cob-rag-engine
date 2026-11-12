import { HttpResponse, http } from "msw";
import type { ScrapeJob, ScrapePage, ScrapePageStatus } from "@/lib/api/scraper-types";
import { CORPORA, SHREE } from "../data";

/**
 * The crawler (scraper service, D8) at the default `scraperUrl`. Stateful: approve moves verified
 * pages to approved, deliver moves approved pages to delivered unless their id is in
 * `scraperState.failing` (then they keep `delivery_error`, like the scraper). A new crawl moves
 * one step per GET of the job: pending → crawling → verifying (review mode) with three pages.
 * Every request's credential header is recorded. Reset in setup.ts. Indian demo data only.
 */
export const SCRAPER = "http://127.0.0.1:8001";

const PATIENT = CORPORA[0]?.id ?? "";
const STAFF = CORPORA[1]?.id ?? "";
const ADMISSIONS = CORPORA[3]?.id ?? "";

export const JOB_FAQ = "a1b2c3d4e5f6";
export const JOB_SOPS = "b2c3d4e5f6a1";
export const JOB_VIDYA = "c3d4e5f6a1b2";
export const PAGE_HOME = "pg0000000001";
export const PAGE_PRICES = "pg0000000002";
export const PAGE_CAREERS = "pg0000000003";
export const PAGE_TIMINGS = "pg0000000004";
export const PAGE_ABOUT = "pg0000000005";
export const LIMIT_ERROR = "RAG engine returned HTTP 409: Your plan allows 5 documents (5 used; this adds 1).";
export const REVIEW_REQUIRED =
	"Review mode is required when you are signed in: pages are delivered when you press Deliver";

interface MockJob {
	job: ScrapeJob;
	/** Statuses still to come, one per GET of the job. */
	script: ScrapeJob["status"][];
}

export interface ScrapeCall {
	method: string;
	path: string;
	auth: "bearer" | "api_key" | "none";
	body: unknown;
}

function job(id: string, over: Partial<ScrapeJob>): ScrapeJob {
	return {
		id,
		url: "https://shree-labs.in/faq",
		max_pages: 50,
		status: "verifying",
		created_at: "2026-09-30T06:30:00Z",
		updated_at: "2026-09-30T06:32:00Z",
		pages_found: 5,
		pages_done: 5,
		error: null,
		target_corpus_id: PATIENT,
		owner_tenant_id: SHREE.id,
		review_mode: true,
		seed_urls: ["https://shree-labs.in/faq"],
		sitemap_url: null,
		...over,
	};
}

function page(id: string, jobId: string, over: Partial<ScrapePage>): ScrapePage {
	return {
		id,
		job_id: jobId,
		url: "https://shree-labs.in/",
		status: "verified",
		title: "",
		word_count: 0,
		content_md: "",
		content_html: "",
		rejection_reason: null,
		fetched_at: "2026-09-30T06:31:00Z",
		extracted_at: "2026-09-30T06:31:02Z",
		delivery_error: null,
		...over,
	};
}

export const MARKDOWN_HOME =
	"# Home collection\n\nWe collect samples at home across Pune: Kothrud, Baner and Aundh.\n\n<script>alert(1)</script> is shown as text.";

function seedPages(): ScrapePage[] {
	return [
		page(PAGE_HOME, JOB_FAQ, {
			url: "https://shree-labs.in/home-collection",
			title: "Home collection | Shree Labs",
			word_count: 420,
		}),
		page(PAGE_PRICES, JOB_FAQ, {
			url: "https://shree-labs.in/prices",
			title: "Test prices | Shree Labs",
			word_count: 610,
		}),
		page(PAGE_CAREERS, JOB_FAQ, {
			url: "https://shree-labs.in/careers",
			title: "Careers",
			word_count: 12,
			status: "rejected",
			rejection_reason: "Too few words (12 < 50)",
		}),
		page(PAGE_TIMINGS, JOB_FAQ, {
			url: "https://shree-labs.in/timings",
			title: "Timings | Shree Labs",
			word_count: 180,
			status: "approved",
			delivery_error: LIMIT_ERROR,
		}),
		page(PAGE_ABOUT, JOB_FAQ, {
			url: "https://shree-labs.in/about",
			title: "About us | Shree Labs",
			word_count: 350,
			status: "delivered",
		}),
	];
}

export const scraperState = {
	jobs: new Map<string, MockJob>(),
	pages: [] as ScrapePage[],
	content: new Map<string, string>(),
	/** Page ids whose delivery fails (the engine's document limit). */
	failing: new Set<string>(),
	calls: [] as ScrapeCall[],
	seq: 0,
};

export function resetScraperState(): void {
	scraperState.jobs = new Map([
		[JOB_FAQ, { job: job(JOB_FAQ, {}), script: [] }],
		[
			JOB_SOPS,
			{
				job: job(JOB_SOPS, {
					url: "https://shree-labs.in/staff",
					target_corpus_id: STAFF,
					status: "completed",
					pages_found: 3,
					pages_done: 3,
					review_mode: false,
					created_at: "2026-09-28T09:00:00Z",
				}),
				script: [],
			},
		],
		[
			JOB_VIDYA,
			{
				job: job(JOB_VIDYA, {
					url: "https://vidyaclasses.in",
					target_corpus_id: ADMISSIONS,
					owner_tenant_id: "33333333-3333-4333-8333-333333333333",
					status: "failed",
					error: "Crawl or delivery failed; see scraper logs",
					pages_found: 0,
					pages_done: 0,
				}),
				script: [],
			},
		],
	]);
	scraperState.pages = seedPages();
	scraperState.content = new Map([[PAGE_HOME, MARKDOWN_HOME]]);
	scraperState.failing = new Set([PAGE_TIMINGS]);
	scraperState.calls = [];
	scraperState.seq = 0;
}
resetScraperState();

function authOf(request: Request): ScrapeCall["auth"] {
	if (request.headers.get("x-api-key")) return "api_key";
	if (/^bearer /i.test(request.headers.get("authorization") ?? "")) return "bearer";
	return "none";
}

async function record(request: Request): Promise<ScrapeCall["auth"]> {
	const text = request.method === "GET" || request.method === "DELETE" ? "" : await request.clone().text();
	const auth = authOf(request);
	scraperState.calls.push({
		method: request.method,
		path: new URL(request.url).pathname + new URL(request.url).search,
		auth,
		body: text ? JSON.parse(text) : null,
	});
	return auth;
}

const noCredential = () => HttpResponse.json({ detail: "Credential required" }, { status: 401 });
const jobNotFound = () => HttpResponse.json({ detail: "Job not found" }, { status: 404 });

function counts(jobId: string): Partial<Record<ScrapePageStatus, number>> {
	const out: Partial<Record<ScrapePageStatus, number>> = {};
	for (const p of scraperState.pages.filter((x) => x.job_id === jobId)) out[p.status] = (out[p.status] ?? 0) + 1;
	return out;
}

/** Move a new crawl one step; on reaching `verifying` its pages appear. */
function advance(m: MockJob): void {
	const next = m.script.shift();
	if (!next) return;
	m.job.status = next;
	if (next === "verifying") {
		const base = m.job.url.replace(/\/+$/, "");
		scraperState.pages.push(
			page(`pn${m.job.id.slice(0, 6)}01`, m.job.id, {
				url: `${base}/fees`,
				title: "Fees 2026-27 | Vidya Classes",
				word_count: 380,
				content_md: "# Fees 2026-27\n\nClass 10 batch: ₹48,000 a year. Call 98220 12345. Open Mon-Sat 8 am to 6 pm.",
			}),
			page(`pn${m.job.id.slice(0, 6)}02`, m.job.id, {
				url: `${base}/batches`,
				title: "Batches | Vidya Classes",
				word_count: 290,
				content_md: "# Batches\n\nMorning and evening batches for classes 8 to 12 at our Kothrud, Pune centre.",
			}),
			page(`pn${m.job.id.slice(0, 6)}03`, m.job.id, {
				url: `${base}/refund-policy`,
				title: "Refund policy | Vidya Classes",
				word_count: 240,
				content_md: "# Refund policy\n\nFees are refundable within 7 days of joining, less a ₹1,000 processing charge.",
			}),
		);
		m.job.pages_found = 3;
		m.job.pages_done = 3;
	}
}

export const scraperHandlers = [
	http.get(`${SCRAPER}/api/v1/jobs`, async ({ request }) => {
		if ((await record(request)) === "none") return noCredential();
		return HttpResponse.json([...scraperState.jobs.values()].map((m) => m.job));
	}),
	http.get(`${SCRAPER}/api/v1/jobs/:id`, async ({ request, params }) => {
		if ((await record(request)) === "none") return noCredential();
		const m = scraperState.jobs.get(String(params.id));
		if (!m) return jobNotFound();
		const out = { ...m.job, page_counts: counts(m.job.id) };
		advance(m);
		return HttpResponse.json(out);
	}),
	http.get(`${SCRAPER}/api/v1/jobs/:id/pages`, async ({ request, params }) => {
		if ((await record(request)) === "none") return noCredential();
		if (!scraperState.jobs.has(String(params.id))) return jobNotFound();
		const status = new URL(request.url).searchParams.get("status");
		return HttpResponse.json(
			scraperState.pages.filter((p) => p.job_id === params.id && (!status || p.status === status)),
		);
	}),
	http.get(`${SCRAPER}/api/v1/jobs/:id/pages/:pageId`, async ({ request, params }) => {
		if ((await record(request)) === "none") return noCredential();
		const p = scraperState.pages.find((x) => x.id === params.pageId && x.job_id === params.id);
		if (!p) return HttpResponse.json({ detail: "Page not found" }, { status: 404 });
		const content = scraperState.content.get(p.id);
		return HttpResponse.json(content === undefined ? p : { ...p, content_file: content });
	}),
	http.post(`${SCRAPER}/api/v1/scrape`, async ({ request }) => {
		const auth = await record(request);
		if (auth === "none") return noCredential();
		const body = (await request.json()) as {
			sitemap_url?: string;
			urls?: string[];
			target_corpus_id?: string;
			review_mode?: boolean;
			max_pages?: number;
		};
		if (auth === "bearer" && body.review_mode === false) {
			return HttpResponse.json({ detail: REVIEW_REQUIRED }, { status: 422 });
		}
		const first = body.urls?.[0] ?? body.sitemap_url ?? "";
		if (/localhost|127\.0\.0\.1|\/\/10\./.test(first)) {
			return HttpResponse.json(
				{ detail: `${first}: private or internal address (10.0.0.0/8) is not allowed` },
				{ status: 422 },
			);
		}
		if (!CORPORA.some((c) => c.id === body.target_corpus_id)) {
			return HttpResponse.json({ detail: "Corpus not found" }, { status: 404 });
		}
		scraperState.seq++;
		const id = `new${String(scraperState.seq).padStart(9, "0")}`;
		scraperState.jobs.set(id, {
			job: job(id, {
				url: first,
				status: "pending",
				target_corpus_id: body.target_corpus_id ?? "",
				review_mode: body.review_mode ?? true,
				max_pages: body.max_pages ?? 50,
				seed_urls: body.urls ?? [],
				sitemap_url: body.sitemap_url ?? null,
				pages_found: 0,
				pages_done: 0,
				created_at: new Date().toISOString(),
			}),
			script: ["crawling", "verifying"],
		});
		return HttpResponse.json({ job_id: id, status: "pending" }, { status: 202 });
	}),
	http.post(`${SCRAPER}/api/v1/jobs/:id/approve`, async ({ request, params }) => {
		if ((await record(request)) === "none") return noCredential();
		if (!scraperState.jobs.has(String(params.id))) return jobNotFound();
		const body = (await request.json()) as { approve_all_verified?: boolean; page_ids?: string[] };
		let approved = 0;
		for (const p of scraperState.pages) {
			if (p.job_id !== params.id || p.status !== "verified") continue;
			if (body.approve_all_verified || body.page_ids?.includes(p.id)) {
				p.status = "approved";
				approved++;
			}
		}
		return HttpResponse.json({ approved });
	}),
	http.post(`${SCRAPER}/api/v1/jobs/:id/deliver`, async ({ request, params }) => {
		if ((await record(request)) === "none") return noCredential();
		const m = scraperState.jobs.get(String(params.id));
		if (!m) return jobNotFound();
		const results: { page_id: string; success: boolean; error?: string }[] = [];
		for (const p of scraperState.pages.filter((x) => x.job_id === params.id && x.status === "approved")) {
			if (scraperState.failing.has(p.id)) {
				p.delivery_error = LIMIT_ERROR;
				results.push({ page_id: p.id, success: false, error: LIMIT_ERROR });
			} else {
				p.status = "delivered";
				p.delivery_error = null;
				results.push({ page_id: p.id, success: true });
			}
		}
		const delivered = results.filter((r) => r.success).length;
		return HttpResponse.json({ attempted: results.length, delivered, failed: results.length - delivered, results });
	}),
	http.delete(`${SCRAPER}/api/v1/jobs/:id`, async ({ request, params }) => {
		if ((await record(request)) === "none") return noCredential();
		if (!scraperState.jobs.delete(String(params.id))) return jobNotFound();
		scraperState.pages = scraperState.pages.filter((p) => p.job_id !== params.id);
		return HttpResponse.json({ deleted: true });
	}),
];
