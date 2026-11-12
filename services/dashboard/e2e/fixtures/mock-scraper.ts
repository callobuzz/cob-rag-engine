import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { BrowserContext, Page, Route } from "@playwright/test";
import type { World } from "./mock-engine";

/**
 * The crawler (scraper service) for Playwright (D8), at `http://scraper.mock`, the `scraperUrl` the
 * mock engine's config.js sets. It follows services/scraper after D8 part A: `X-API-Key` first,
 * else `Authorization: Bearer` (the engine's codes such as `token_expired` passed through), jobs
 * visible to their tenant (admin: all), review mode required for sign-ins (422), approve and
 * deliver with per-page errors. "Crawling" reads the fixture site in e2e/fixtures/site (Vidya
 * Classes) by path, whatever host was typed; delivered pages become documents in the mock engine
 * and count against the tenant's document limit (A6), like the real ingest.
 */

export const MOCK_SCRAPER = "http://scraper.mock";
export const REVIEW_REQUIRED =
	"Review mode is required when you are signed in: pages are delivered when you press Deliver";
/** How long a crawl stays `pending`, then `crawling`, before it is ready. */
export const CRAWL_MS = { pending: 400, crawling: 1600 };
const MIN_WORDS = 50;

type JobStatus = "pending" | "crawling" | "extracting" | "verifying" | "delivering" | "completed" | "failed";
type PageStatus = "queued" | "fetched" | "extracted" | "verified" | "rejected" | "approved" | "delivered" | "failed";

interface ScrapeJob {
	id: string;
	url: string;
	max_pages: number;
	status: JobStatus;
	created_at: string;
	updated_at: string;
	pages_found: number;
	pages_done: number;
	error: string | null;
	target_corpus_id: string;
	owner_tenant_id: string | null;
	review_mode: boolean;
	seed_urls: string[];
	sitemap_url: string | null;
}
interface ScrapePage {
	id: string;
	job_id: string;
	url: string;
	status: PageStatus;
	title: string;
	word_count: number;
	content_md: string;
	content_html: string;
	rejection_reason: string | null;
	fetched_at: string | null;
	extracted_at: string | null;
	delivery_error: string | null;
}
interface JobRecord {
	job: ScrapeJob;
	startedAt: number;
	/** Addresses to crawl (seeds, then the sitemap's), at most max_pages. */
	urls: string[];
	finished: boolean;
	/** The credential that started it, for review-mode-off delivery (keys only). */
	credential: Caller | null;
}

export interface ScraperState {
	jobs: JobRecord[];
	pages: ScrapePage[];
	markdown: Map<string, string>;
	/** Every request: method, path and which credential header it carried. */
	calls: { method: string; path: string; auth: "api_key" | "bearer" | "none" }[];
}

const states = new WeakMap<World, ScraperState>();

/** The mock scraper's state for a world (created on first use). */
export function scraperOf(world: World): ScraperState {
	let s = states.get(world);
	if (!s) {
		s = { jobs: [], pages: [], markdown: new Map(), calls: [] };
		states.set(world, s);
	}
	return s;
}

const SITE_DIR = join(dirname(fileURLToPath(import.meta.url)), "site");
const SITE_FILES: Record<string, string> = {
	"/": "index.html",
	"/fees": "fees.html",
	"/batches": "batches.html",
	"/refund-policy": "refund-policy.html",
	"/contact": "contact.html",
};

/** Title and Markdown-ish text of a fixture page, like trafilatura's output: headings and paragraphs. */
function readSitePage(path: string): { title: string; markdown: string } | null {
	const file = SITE_FILES[path.replace(/\/+$/, "") || "/"];
	if (!file) return null;
	const html = readFileSync(join(SITE_DIR, file), "utf8");
	const title = /<title>([^<]*)<\/title>/.exec(html)?.[1]?.trim() ?? "";
	const main = /<main>([\s\S]*?)<\/main>/.exec(html)?.[1] ?? html;
	const markdown = main
		.replace(/<h1>([\s\S]*?)<\/h1>/g, "# $1\n\n")
		.replace(/<li>([\s\S]*?)<\/li>/g, "- $1\n")
		.replace(/<p>([\s\S]*?)<\/p>/g, "$1\n\n")
		.replace(/<[^>]+>/g, "")
		.replace(/[ \t]*\n[ \t]*/g, "\n")
		.replace(/([^\n])\n(?=[^\n#-])/g, "$1 ")
		.replace(/\n{3,}/g, "\n\n")
		.trim();
	return { title, markdown };
}

function sitemapPaths(): string[] {
	const xml = readFileSync(join(SITE_DIR, "sitemap.xml"), "utf8");
	return [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => new URL(m[1] ?? "/").pathname);
}

const words = (text: string) => text.split(/\s+/).filter(Boolean).length;
const iso = (ms: number) => new Date(ms).toISOString();
const shortId = () => randomUUID().replace(/-/g, "").slice(0, 12);

type Caller =
	| { kind: "admin"; auth: "api_key" | "bearer"; header: Record<string, string> }
	| { kind: "tenant"; tenantId: string; auth: "api_key" | "bearer"; header: Record<string, string> };

/** Same rules as the scraper's auth after D8 part A (and the engine's whoami behind it). */
function resolve(w: World, headers: Record<string, string>): Caller | { error: number; body: Record<string, unknown> } {
	const key = headers["x-api-key"];
	if (key) {
		const k = w.keys.get(key);
		if (!k) return { error: 401, body: { detail: "Invalid API key", code: "invalid_api_key" } };
		if (k.kind === "admin") return { kind: "admin", auth: "api_key", header: { "x-api-key": key } };
		const t = w.tenants.find((x) => x.id === k.tenantId);
		if (t && t.status !== "active")
			return {
				error: 403,
				body: { detail: "This workspace is not active", code: "workspace_not_active", status: t.status },
			};
		// Corpus keys don't open the dashboard; treat them as their tenant for the mock.
		return { kind: "tenant", tenantId: k.tenantId ?? "", auth: "api_key", header: { "x-api-key": key } };
	}
	const authz = headers.authorization ?? "";
	const m = /^bearer\s+(\S+)$/i.exec(authz.trim());
	if (!m?.[1]) return { error: 401, body: { detail: "Credential required" } };
	const a = w.access.get(m[1]);
	if (!a) return { error: 401, body: { detail: "Invalid token", code: "invalid_token" } };
	if (a.expired) return { error: 401, body: { detail: "Access token expired", code: "token_expired" } };
	const s = w.sessions.get(a.sid);
	const u = s ? w.users.find((x) => x.id === s.userId) : undefined;
	if (!s || s.revoked || !u) return { error: 401, body: { detail: "Session ended", code: "session_revoked" } };
	const header = { authorization: `Bearer ${m[1]}` };
	if (u.superadmin) return { kind: "admin", auth: "bearer", header };
	const t = w.tenants.find((x) => x.id === s.tenantId);
	if (!t) return { error: 401, body: { detail: "Invalid token", code: "invalid_token" } };
	if (t.status !== "active")
		return {
			error: 403,
			body: { detail: "This workspace is not active", code: "workspace_not_active", status: t.status },
		};
	return { kind: "tenant", tenantId: t.id, auth: "bearer", header };
}

const canSee = (c: Caller, j: ScrapeJob) =>
	c.kind === "admin" || (!!j.owner_tenant_id && j.owner_tenant_id === c.tenantId);

/** Move a job along by time: pending → crawling (pages appear one by one) → verifying. */
function tick(w: World, s: ScraperState, r: JobRecord, now: number): void {
	if (r.finished) return;
	const t = now - r.startedAt;
	if (t < CRAWL_MS.pending) return;
	r.job.status = "crawling";
	r.job.pages_found = r.urls.length;
	const share = Math.min(1, (t - CRAWL_MS.pending) / CRAWL_MS.crawling);
	const due = t >= CRAWL_MS.pending + CRAWL_MS.crawling ? r.urls.length : Math.floor(share * r.urls.length);
	const have = s.pages.filter((p) => p.job_id === r.job.id).length;
	for (const url of r.urls.slice(have, due)) {
		const id = shortId();
		const read = readSitePage(new URL(url).pathname);
		const page: ScrapePage = {
			id,
			job_id: r.job.id,
			url,
			status: "failed",
			title: read?.title ?? "",
			word_count: read ? words(read.markdown) : 0,
			content_md: "",
			content_html: "",
			rejection_reason: read ? null : "Fetch failed",
			fetched_at: iso(now),
			extracted_at: read ? iso(now) : null,
			delivery_error: null,
		};
		if (read) {
			s.markdown.set(id, read.markdown);
			if (page.word_count < MIN_WORDS) {
				page.status = "rejected";
				page.rejection_reason = `Too few words (${page.word_count} < ${MIN_WORDS})`;
			} else page.status = "verified";
		}
		s.pages.push(page);
	}
	r.job.updated_at = iso(now);
	if (due < r.urls.length) return;
	r.finished = true;
	r.job.status = "verifying";
	r.job.pages_done = r.urls.length;
	if (!r.job.review_mode && r.credential) {
		for (const p of s.pages) if (p.job_id === r.job.id && p.status === "verified") p.status = "approved";
		const results = deliver(w, s, r.job);
		const failed = results.filter((x) => !x.success).length;
		r.job.status = "completed";
		r.job.error = failed ? `${failed} of ${results.length} pages failed delivery` : null;
	}
}

function fileName(title: string): string {
	const safe = title
		.toLowerCase()
		.replace(/[^\w-]/g, "-")
		.replace(/-+/g, "-")
		.replace(/^-|-$/g, "")
		.slice(0, 80);
	return `${safe || "untitled"}.md`;
}

/** Approved pages → engine documents, one by one, stopping at nothing: failures stay approved. */
function deliver(w: World, s: ScraperState, job: ScrapeJob) {
	const results: { page_id: string; success: boolean; error?: string }[] = [];
	const tenantId = w.kbs.items.find((k) => k.id === job.target_corpus_id)?.tenant_id;
	for (const p of s.pages.filter((x) => x.job_id === job.id && x.status === "approved")) {
		const limit = tenantId ? w.docs.limits.get(tenantId) : undefined;
		const used = w.docs.docs.filter(
			(d) => w.docs.tenantOf(d.corpusId) === tenantId && d.versions.some((v) => v.status === "active"),
		).length;
		if (limit != null && used + 1 > limit) {
			const error = `RAG engine returned HTTP 409: Document limit reached (${limit})`;
			p.delivery_error = error;
			results.push({ page_id: p.id, success: false, error });
			continue;
		}
		const now = Date.now();
		const text = s.markdown.get(p.id) ?? "";
		const parent = randomUUID();
		w.docs.docs.unshift({
			id: randomUUID(),
			corpusId: job.target_corpus_id,
			file_name: fileName(p.title),
			mime_type: "text/markdown",
			size_bytes: text.length,
			ingested_at: iso(now),
			external_id: null,
			metadata: null,
			source_uri: null,
			versions: [
				{
					id: randomUUID(),
					status: "active",
					parser_version: "markdown",
					chunk_schema_version: "2",
					created_at: iso(now),
					chunks: text
						.split(/\n\n+/)
						.filter(Boolean)
						.map((chunk, i) => ({
							id: i === 0 ? parent : randomUUID(),
							chunk_index: i,
							text: chunk,
							page: null,
							token_count: Math.ceil(chunk.length / 4),
							is_table: false,
							is_code: false,
							parent_chunk_id: i > 0 ? parent : null,
							section: [p.title],
						})),
				},
			],
		});
		w.docs.onChange(job.target_corpus_id);
		p.status = "delivered";
		p.delivery_error = null;
		results.push({ page_id: p.id, success: true });
	}
	return results;
}

const CORS = {
	"access-control-allow-origin": "*",
	"access-control-allow-headers": "authorization, x-api-key, content-type",
	"access-control-allow-methods": "GET, POST, DELETE, OPTIONS",
};

function json(route: Route, status: number, body: unknown) {
	return route.fulfill({
		status,
		headers: { ...CORS, "content-type": "application/json" },
		body: JSON.stringify(body),
	});
}

export async function installMockScraper(target: Page | BrowserContext, world: World): Promise<void> {
	await target.route(`${MOCK_SCRAPER}/**`, async (route) => {
		const req = route.request();
		const method = req.method();
		if (method === "OPTIONS") return route.fulfill({ status: 204, headers: CORS });
		const url = new URL(req.url());
		const path = url.pathname;
		const headers = await req.allHeaders();
		const s = scraperOf(world);
		s.calls.push({
			method,
			path: path + url.search,
			auth: headers["x-api-key"] ? "api_key" : headers.authorization ? "bearer" : "none",
		});
		const who = resolve(world, headers);
		if ("error" in who) return json(route, who.error, who.body);
		const now = Date.now();
		for (const r of s.jobs) tick(world, s, r, now);
		const visible = (id: string) => s.jobs.find((r) => r.job.id === id && canSee(who, r.job));
		let body: Record<string, unknown> = {};
		try {
			body = (req.postDataJSON() as Record<string, unknown>) ?? {};
		} catch {
			body = {};
		}

		if (method === "POST" && path === "/api/v1/scrape") {
			if (who.auth === "bearer" && body.review_mode === false) return json(route, 422, { detail: REVIEW_REQUIRED });
			const kbId = String(body.target_corpus_id ?? "");
			const kb = world.kbs.items.find((k) => k.id === kbId);
			if (!kb) return json(route, 404, { detail: "Corpus not found" });
			if (who.kind === "tenant" && kb.tenant_id !== who.tenantId)
				return json(route, 403, { detail: "This key cannot use that corpus" });
			const seeds = [...new Set(((body.urls as string[] | undefined) ?? []).map((u) => u.trim()).filter(Boolean))];
			const sitemap = typeof body.sitemap_url === "string" && body.sitemap_url.trim() ? body.sitemap_url.trim() : null;
			for (const u of [...seeds, ...(sitemap ? [sitemap] : [])]) {
				const host = new URL(u).hostname;
				if (host === "localhost" || /^(127|10|192\.168)\./.test(host))
					return json(route, 422, { detail: `${u}: private or internal address is not allowed` });
			}
			const max = Number(body.max_pages ?? 50);
			const fromSitemap = sitemap ? sitemapPaths().map((p) => new URL(p, sitemap).toString()) : [];
			const urls = [...new Set([...seeds, ...fromSitemap])].slice(0, max);
			const job: ScrapeJob = {
				id: shortId(),
				url: seeds[0] ?? sitemap ?? "",
				max_pages: max,
				status: "pending",
				created_at: iso(now),
				updated_at: iso(now),
				pages_found: 0,
				pages_done: 0,
				error: null,
				target_corpus_id: kbId,
				owner_tenant_id: who.kind === "admin" ? null : who.tenantId,
				review_mode: body.review_mode !== false,
				seed_urls: seeds,
				sitemap_url: sitemap,
			};
			s.jobs.push({ job, startedAt: now, urls, finished: false, credential: who });
			return json(route, 202, { job_id: job.id, status: job.status });
		}
		if (method === "GET" && path === "/api/v1/jobs") {
			return json(
				route,
				200,
				s.jobs.filter((r) => canSee(who, r.job)).map((r) => r.job),
			);
		}
		const m = /^\/api\/v1\/jobs\/([^/]+)(\/.*)?$/.exec(path);
		if (!m) return json(route, 404, { detail: "Not Found" });
		const r = visible(decodeURIComponent(m[1] ?? ""));
		if (!r) return json(route, 404, { detail: "Job not found" });
		const rest = m[2] ?? "";
		const pagesOf = () => s.pages.filter((p) => p.job_id === r.job.id);
		if (method === "GET" && rest === "") {
			const counts: Record<string, number> = {};
			for (const p of pagesOf()) counts[p.status] = (counts[p.status] ?? 0) + 1;
			return json(route, 200, { ...r.job, page_counts: counts });
		}
		if (method === "DELETE" && rest === "") {
			s.jobs = s.jobs.filter((x) => x !== r);
			s.pages = s.pages.filter((p) => p.job_id !== r.job.id);
			return json(route, 200, { deleted: true });
		}
		if (method === "GET" && rest === "/pages") {
			const status = url.searchParams.get("status");
			return json(
				route,
				200,
				pagesOf().filter((p) => !status || p.status === status),
			);
		}
		const pm = /^\/pages\/([^/]+)$/.exec(rest);
		if (method === "GET" && pm) {
			const p = pagesOf().find((x) => x.id === pm[1]);
			if (!p) return json(route, 404, { detail: "Page not found" });
			const md = s.markdown.get(p.id);
			return json(route, 200, md === undefined ? p : { ...p, content_file: md });
		}
		if (method === "POST" && rest === "/approve") {
			const ids = new Set((body.page_ids as string[] | undefined) ?? []);
			let approved = 0;
			for (const p of pagesOf()) {
				if (p.status === "verified" && (body.approve_all_verified === true || ids.has(p.id))) {
					p.status = "approved";
					approved++;
				}
			}
			return json(route, 200, { approved });
		}
		if (method === "POST" && rest === "/deliver") {
			if (!r.job.target_corpus_id) return json(route, 409, { detail: "Job has no target corpus" });
			const results = deliver(world, s, r.job);
			const delivered = results.filter((x) => x.success).length;
			return json(route, 200, {
				attempted: results.length,
				delivered,
				failed: results.length - delivered,
				results,
			});
		}
		return json(route, 404, { detail: "Not Found" });
	});
}
