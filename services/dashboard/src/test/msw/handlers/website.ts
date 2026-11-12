import { HttpResponse, http } from "msw";
import type { WebsiteOut, WebsitePage, WebsiteSource } from "@/features/website/api";
import { BASE, CORPORA } from "../data";

/**
 * Website learning (BIZ1), stateful per test: the overview, learn, re-read (with the engine's
 * ten-minute cooldown), remove a site, the page list (filter, search, paging, counts) and the
 * owner's per-page controls. Contract: docs/openapi.json `/corpora/{id}/website*`. Not in the
 * default server (the Knowledge page mounts it later); tests call `useWebsiteHandlers()`.
 */

const KB = CORPORA[0]?.id ?? "";
export const SOURCE_ID = "6a000000-0000-4000-8000-000000000001";
const NOW = "2026-10-05T10:00:00Z";

function page(n: number, path: string, over: Partial<WebsitePage> = {}): WebsitePage {
	return {
		id: `6b000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
		source_id: SOURCE_ID,
		url: `https://shreedental.in${path}`,
		title: null,
		status: "learned",
		skip_reason: null,
		duplicate_of: null,
		http_status: 200,
		depth: 1,
		words: 240,
		in_sitemap: true,
		excluded_by_owner: false,
		document_id: null,
		etag: null,
		last_modified: null,
		content_hash: null,
		last_seen_at: NOW,
		last_checked_at: NOW,
		last_changed_at: NOW,
		check_count: 1,
		change_count: 0,
		...over,
	};
}

export const SOURCE: WebsiteSource = {
	id: SOURCE_ID,
	origin: "https://shreedental.in",
	scope: "shreedental.in",
	status: "ready",
	pause_reason: null,
	pages_found: 6,
	pages_learned: 3,
	pages_skipped: 3,
	first_pass_done_at: NOW,
	last_run_started_at: NOW,
	last_run_finished_at: NOW,
	last_error_code: null,
};

export const PAGES: WebsitePage[] = [
	page(1, "/", { title: "Shree Dental Clinic, Pune" }),
	page(2, "/pricing", { title: "Fees and charges" }),
	page(3, "/services/root-canal", { title: "Root canal treatment" }),
	page(4, "/cart", { status: "skipped", skip_reason: "private_page", words: null }),
	page(5, "/services/root-canal-2", { status: "skipped", skip_reason: "duplicate", title: "Root canal treatment" }),
	page(6, "/old-offer", { status: "failed", skip_reason: "http_error", http_status: 404, words: null }),
];

interface State {
	site: WebsiteOut;
	/** BIZ2: the one push key the mock ever made (the key itself is never in `site`). */
	pushKey: string | null;
	pages: WebsitePage[];
	rereadAt: number | null;
	calls: { method: string; path: string; body: unknown }[];
}

function fresh(): State {
	return {
		site: {
			settings: {
				enabled: true,
				exclude_patterns: [],
				last_reread_at: null,
				refresh_mode: "daily", // BIZ2
				adaptive: true,
			},
			plan: { learning_enabled: true, pages_cap: 50, pages_learned: 3 },
			sources: [{ ...SOURCE }],
			unlearned_origins: [],
			learn_key: { set: false, hint: null, created_at: null }, // BIZ2
		},
		pushKey: null,
		pages: PAGES.map((p) => ({ ...p })),
		rereadAt: null,
		calls: [],
	};
}

export let websiteState: State = fresh();
export function resetWebsiteState(): void {
	websiteState = fresh();
}

const COOLDOWN_S = 600;
const notFound = (code: string) => HttpResponse.json({ detail: "Not found", code }, { status: 404 });
const root = `${BASE}/api/v1/corpora/:id/website`;

export const websiteHandlers = [
	http.get(root, () => HttpResponse.json(websiteState.site)),
	http.patch(root, async ({ request }) => {
		const body = (await request.json()) as {
			enabled?: boolean;
			exclude_patterns?: string[];
			refresh_mode?: string; // BIZ2
			adaptive?: boolean; // BIZ2
		};
		websiteState.calls.push({ method: "PATCH", path: "/website", body });
		if (body.refresh_mode !== undefined) websiteState.site.settings.refresh_mode = body.refresh_mode; // BIZ2
		if (body.adaptive !== undefined) websiteState.site.settings.adaptive = body.adaptive; // BIZ2
		if (body.enabled !== undefined) websiteState.site.settings.enabled = body.enabled;
		if (body.exclude_patterns) websiteState.site.settings.exclude_patterns = body.exclude_patterns;
		return HttpResponse.json(websiteState.site);
	}),
	http.post(`${root}/learn`, async ({ request }) => {
		const body = (await request.json()) as { origin: string };
		websiteState.calls.push({ method: "POST", path: "/website/learn", body });
		const source: WebsiteSource = {
			...SOURCE,
			id: "6a000000-0000-4000-8000-000000000002",
			origin: body.origin,
			scope: body.origin.replace(/^https?:\/\//, ""),
			status: "discovering",
			pages_found: 0,
			pages_learned: 0,
			pages_skipped: 0,
			first_pass_done_at: null,
			last_run_finished_at: null,
		};
		websiteState.site.sources.push(source);
		websiteState.site.unlearned_origins = websiteState.site.unlearned_origins.filter((o) => o !== body.origin);
		return HttpResponse.json(source, { status: 202 });
	}),
	http.post(`${root}/reread`, () => {
		websiteState.calls.push({ method: "POST", path: "/website/reread", body: null });
		if (websiteState.rereadAt !== null) {
			return HttpResponse.json(
				{
					detail: "You can read your website again in 10 minutes.",
					code: "website_reread_too_soon",
					retry_after: COOLDOWN_S,
				},
				{ status: 429, headers: { "Retry-After": String(COOLDOWN_S) } },
			);
		}
		websiteState.rereadAt = Date.now();
		for (const s of websiteState.site.sources) s.status = "discovering";
		return HttpResponse.json({ queued: websiteState.site.sources.length }, { status: 202 });
	}),
	http.delete(`${root}/sources/:sid`, ({ params }) => {
		websiteState.calls.push({ method: "DELETE", path: `/website/sources/${String(params.sid)}`, body: null });
		const had = websiteState.site.sources.length;
		websiteState.site.sources = websiteState.site.sources.filter((s) => s.id !== params.sid);
		return websiteState.site.sources.length === had
			? notFound("website_source_not_found")
			: new HttpResponse(null, { status: 204 });
	}),
	// BIZ2: the push link, shown once
	http.post(`${root}/learn-key`, () => {
		websiteState.calls.push({ method: "POST", path: "/website/learn-key", body: null });
		const key = `lk_${"pQ7xK2mV9aTnR4sW".repeat(2)}`;
		websiteState.pushKey = key;
		websiteState.site.learn_key = { set: true, hint: key.slice(-4), created_at: NOW };
		return HttpResponse.json(
			{ key, push_url: `https://rag.example.in/api/v1/public/learn/${key}`, hint: key.slice(-4) },
			{ status: 201 },
		);
	}),
	http.delete(`${root}/learn-key`, () => {
		websiteState.calls.push({ method: "DELETE", path: "/website/learn-key", body: null });
		websiteState.pushKey = null;
		websiteState.site.learn_key = { set: false, hint: null, created_at: null };
		return new HttpResponse(null, { status: 204 });
	}),
	// /BIZ2
	http.get(`${root}/pages`, ({ request }) => {
		const url = new URL(request.url);
		const status = url.searchParams.get("status");
		const q = (url.searchParams.get("q") ?? "").toLowerCase();
		const offset = Number(url.searchParams.get("offset") ?? 0);
		const limit = Math.min(100, Number(url.searchParams.get("limit") ?? 50));
		let rows = websiteState.pages;
		const counts: Record<string, number> = {};
		for (const p of rows) counts[p.status] = (counts[p.status] ?? 0) + 1;
		if (status === "waiting") rows = rows.filter((p) => p.status === "queued" || p.status === "discovered");
		else if (status) rows = rows.filter((p) => p.status === status);
		if (q) rows = rows.filter((p) => p.url.toLowerCase().includes(q) || (p.title ?? "").toLowerCase().includes(q));
		return HttpResponse.json({ items: rows.slice(offset, offset + limit), total: rows.length, counts });
	}),
	http.post(`${root}/pages/:pid/reread`, ({ params }) => {
		websiteState.calls.push({ method: "POST", path: `/website/pages/${String(params.pid)}/reread`, body: null });
		const p = websiteState.pages.find((x) => x.id === params.pid);
		return p ? HttpResponse.json(p, { status: 202 }) : notFound("website_page_not_found");
	}),
	http.patch(`${root}/pages/:pid`, async ({ params, request }) => {
		const body = (await request.json()) as { excluded: boolean };
		websiteState.calls.push({ method: "PATCH", path: `/website/pages/${String(params.pid)}`, body });
		const p = websiteState.pages.find((x) => x.id === params.pid);
		if (!p) return notFound("website_page_not_found");
		p.excluded_by_owner = body.excluded;
		p.status = body.excluded ? "excluded" : "queued";
		p.skip_reason = body.excluded ? "owner" : null;
		return HttpResponse.json(p);
	}),
];

export { KB as WEBSITE_KB };
