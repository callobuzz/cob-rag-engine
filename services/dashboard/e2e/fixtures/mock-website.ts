/**
 * Website-learning part of the fake engine (BIZ1): `GET/PATCH /corpora/{id}/website`, learn,
 * re-read (with the engine's ten-minute cooldown), remove a site, the page list and the owner's
 * per-page controls. Follows `api/routes/website.py` closely enough for the dashboard's real
 * behaviour: `owner_required` for a member on the owner-only routes, `website_*` error codes,
 * counts per status, the "waiting" chip. Indian demo data only.
 */

import { randomUUID } from "node:crypto";

export interface MockSource {
	id: string;
	origin: string;
	scope: string;
	status: "idle" | "discovering" | "learning" | "ready" | "paused" | "blocked" | "failed";
	pause_reason: string | null;
	pages_found: number;
	pages_learned: number;
	pages_skipped: number;
	first_pass_done_at: string | null;
	last_run_started_at: string | null;
	last_run_finished_at: string | null;
	last_error_code: string | null;
}

export interface MockPage {
	id: string;
	source_id: string;
	corpus_id: string;
	url: string;
	title: string | null;
	status: string;
	skip_reason: string | null;
	duplicate_of: string | null;
	http_status: number | null;
	depth: number;
	words: number | null;
	in_sitemap: boolean;
	excluded_by_owner: boolean;
	document_id: string | null;
	etag: string | null;
	last_modified: string | null;
	content_hash: string | null;
	last_seen_at: string | null;
	last_checked_at: string | null;
	last_changed_at: string | null;
	check_count: number;
	change_count: number;
}

export interface KbWebsite {
	enabled: boolean;
	exclude_patterns: string[];
	last_reread_at: string | null;
	// BIZ2 freshness
	refresh_mode: string;
	adaptive: boolean;
	learn_key: { set: boolean; hint: string | null; created_at: string | null };
	// /BIZ2 freshness
	sources: MockSource[];
	pages: MockPage[];
	/** Allowed sites of the knowledge base's widget keys that are not read yet. */
	unlearned_origins: string[];
}

export interface WebsiteState {
	/** Knowledge base id → its website learning. */
	kbs: Map<string, KbWebsite>;
	/** The plan (every workspace of the mock): learning on, cap on pages. */
	plan: { learning_enabled: boolean; pages_cap: number };
	/** Writes the dashboard sent (for assertions). */
	writes: { method: string; path: string; body: unknown }[];
}

const PATIENT = "7b221003-0000-4000-8000-000000000001";
const SOURCE = "6a000000-0000-4000-8000-000000000001";
const NOW = "2026-10-05T10:00:00Z";
const COOLDOWN_MS = 10 * 60 * 1000;

function page(n: number, path: string, over: Partial<MockPage> = {}): MockPage {
	return {
		id: `6b000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
		source_id: SOURCE,
		corpus_id: PATIENT,
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

export function createWebsiteState(): WebsiteState {
	const source: MockSource = {
		id: SOURCE,
		origin: "https://shreedental.in",
		scope: "shreedental.in",
		status: "ready",
		pause_reason: null,
		pages_found: 5,
		pages_learned: 3,
		pages_skipped: 2,
		first_pass_done_at: NOW,
		last_run_started_at: NOW,
		last_run_finished_at: NOW,
		last_error_code: null,
	};
	return {
		kbs: new Map([
			[
				PATIENT,
				{
					enabled: true,
					exclude_patterns: [],
					last_reread_at: null,
					refresh_mode: "daily", // BIZ2
					adaptive: true,
					learn_key: { set: false, hint: null, created_at: null },
					sources: [source],
					unlearned_origins: [],
					pages: [
						page(1, "/", { title: "Shree Dental Clinic, Pune" }),
						page(2, "/pricing", { title: "Fees and charges" }),
						page(3, "/services/root-canal", { title: "Root canal treatment" }),
						page(4, "/cart", { status: "skipped", skip_reason: "private_page", words: null }),
						page(5, "/services/root-canal-2", { status: "skipped", skip_reason: "duplicate" }),
					],
				},
			],
		]),
		plan: { learning_enabled: true, pages_cap: 50 },
		writes: [],
	};
}

export interface WebsiteCtx {
	role: "owner" | "member" | null;
	canUse: (corpusId: string) => boolean;
	reply: (status: number, body?: unknown, headers?: Record<string, string>) => Promise<void>;
}

function kbOf(s: WebsiteState, id: string): KbWebsite {
	let kb = s.kbs.get(id);
	if (!kb) {
		kb = {
			enabled: true,
			exclude_patterns: [],
			last_reread_at: null,
			refresh_mode: "daily", // BIZ2
			adaptive: true,
			learn_key: { set: false, hint: null, created_at: null },
			sources: [],
			pages: [],
			unlearned_origins: [],
		};
		s.kbs.set(id, kb);
	}
	return kb;
}

const pageOut = ({ corpus_id: _c, ...rest }: MockPage) => rest;

// BIZ2 freshness
const REFRESH_MODES = ["off", "monthly", "weekly", "daily", "6h", "hourly"];

/** One step of a read in progress: finding pages, then learning them, then up to date. */
function advance(kb: KbWebsite): void {
	for (const x of kb.sources) {
		if (x.status === "discovering") {
			x.status = "learning";
		} else if (x.status === "learning") {
			x.status = "ready";
			for (const p of kb.pages) if (p.source_id === x.id && p.status === "queued") p.status = "learned";
			x.pages_learned = kb.pages.filter((p) => p.source_id === x.id && p.status === "learned").length;
			x.first_pass_done_at = new Date().toISOString();
		}
	}
}
// /BIZ2 freshness

/** `/api/v1/corpora/{id}/website…`; returns false for other paths. */
export async function handleWebsiteRoute(
	s: WebsiteState,
	method: string,
	path: string,
	fullUrl: string,
	body: Record<string, unknown>,
	ctx: WebsiteCtx,
): Promise<boolean> {
	const m = /^\/api\/v1\/corpora\/([^/]+)\/website(\/.*)?$/.exec(path);
	if (!m) return false;
	const corpusId = m[1] ?? "";
	const rest = m[2] ?? "";
	const done = (status: number, payload?: unknown, headers?: Record<string, string>) =>
		ctx.reply(status, payload, headers).then(() => true);
	if (!ctx.canUse(corpusId)) return done(403, { detail: "Not allowed to access this corpus" });
	const kb = kbOf(s, corpusId);
	const ownerErr = () => done(403, { detail: "Only a workspace owner can do this", code: "owner_required" });
	const notOwner = ctx.role === "member";
	const log = () => s.writes.push({ method, path: rest || "/", body });
	const overview = () => {
		advance(kb); // BIZ2: a site that is being read moves on with every look
		const learned = kb.sources.reduce((n, x) => n + x.pages_learned, 0);
		return {
			settings: {
				enabled: kb.enabled,
				exclude_patterns: kb.exclude_patterns,
				last_reread_at: kb.last_reread_at,
				refresh_mode: kb.refresh_mode, // BIZ2
				adaptive: kb.adaptive,
			},
			learn_key: kb.learn_key, // BIZ2
			plan: { learning_enabled: s.plan.learning_enabled, pages_cap: s.plan.pages_cap, pages_learned: learned },
			sources: kb.sources,
			unlearned_origins: kb.unlearned_origins,
		};
	};

	if (rest === "" && method === "GET") return done(200, overview());
	if (rest === "" && method === "PATCH") {
		if (notOwner) return ownerErr();
		log();
		if (typeof body.refresh_mode === "string") {
			// BIZ2
			if (!REFRESH_MODES.includes(body.refresh_mode)) return done(422, { detail: "Unknown schedule" });
			kb.refresh_mode = body.refresh_mode;
		}
		if (typeof body.adaptive === "boolean") kb.adaptive = body.adaptive; // BIZ2
		if (typeof body.enabled === "boolean") kb.enabled = body.enabled;
		if (Array.isArray(body.exclude_patterns)) kb.exclude_patterns = body.exclude_patterns as string[];
		return done(200, overview());
	}
	if (rest === "/learn" && method === "POST") {
		log();
		const origin = String(body.origin ?? "");
		if (!kb.unlearned_origins.includes(origin) && !kb.sources.some((x) => x.origin === origin)) {
			return done(422, { detail: "Add this site to a widget key first.", code: "website_not_allowed" });
		}
		kb.unlearned_origins = kb.unlearned_origins.filter((o) => o !== origin);
		const source: MockSource = {
			id: randomUUID(),
			origin,
			scope: origin.replace(/^https?:\/\//, ""),
			status: "discovering",
			pause_reason: null,
			pages_found: 0,
			pages_learned: 0,
			pages_skipped: 0,
			first_pass_done_at: null,
			last_run_started_at: new Date().toISOString(),
			last_run_finished_at: null,
			last_error_code: null,
		};
		kb.sources.push(source);
		return done(202, source);
	}
	if (rest === "/reread" && method === "POST") {
		log();
		const last = kb.last_reread_at ? Date.parse(kb.last_reread_at) : 0;
		if (last && Date.now() - last < COOLDOWN_MS) {
			const wait = Math.ceil((last + COOLDOWN_MS - Date.now()) / 1000);
			return done(
				429,
				{
					detail: "You can read your website again in a few minutes.",
					code: "website_reread_too_soon",
					retry_after: wait,
				},
				{ "retry-after": String(wait) },
			);
		}
		kb.last_reread_at = new Date().toISOString();
		for (const x of kb.sources) x.status = "discovering";
		return done(202, { queued: kb.sources.length });
	}
	// BIZ2: the push link (owner only; the key is in the creation response only)
	if (rest === "/learn-key" && method === "POST") {
		if (notOwner) return ownerErr();
		log();
		const key = `lk_${randomUUID().replace(/-/g, "").slice(0, 32)}`;
		kb.learn_key = { set: true, hint: key.slice(-4), created_at: new Date().toISOString() };
		return done(201, { key, push_url: `https://rag.example.in/api/v1/public/learn/${key}`, hint: key.slice(-4) });
	}
	if (rest === "/learn-key" && method === "DELETE") {
		if (notOwner) return ownerErr();
		log();
		kb.learn_key = { set: false, hint: null, created_at: null };
		return done(204);
	}
	// /BIZ2
	const source = /^\/sources\/([^/]+)$/.exec(rest);
	if (source && method === "DELETE") {
		if (notOwner) return ownerErr();
		log();
		const found = kb.sources.find((x) => x.id === source[1]);
		if (!found) return done(404, { detail: "Not found", code: "website_source_not_found" });
		kb.sources = kb.sources.filter((x) => x.id !== found.id);
		kb.pages = kb.pages.filter((p) => p.source_id !== found.id);
		return done(204);
	}
	if (rest === "/pages" && method === "GET") {
		const q = new URL(fullUrl, "http://mock").searchParams;
		const status = q.get("status");
		const search = (q.get("q") ?? "").toLowerCase();
		const offset = Number(q.get("offset") ?? 0);
		const limit = Math.min(100, Number(q.get("limit") ?? 50));
		const counts: Record<string, number> = {};
		for (const p of kb.pages) counts[p.status] = (counts[p.status] ?? 0) + 1;
		let rows = kb.pages;
		if (status === "waiting") rows = rows.filter((p) => p.status === "queued" || p.status === "discovered");
		else if (status) rows = rows.filter((p) => p.status === status);
		if (search)
			rows = rows.filter((p) => p.url.toLowerCase().includes(search) || (p.title ?? "").toLowerCase().includes(search));
		return done(200, { items: rows.slice(offset, offset + limit).map(pageOut), total: rows.length, counts });
	}
	const one = /^\/pages\/([^/]+)(\/reread)?$/.exec(rest);
	if (one) {
		const found = kb.pages.find((p) => p.id === one[1]);
		if (!found) return done(404, { detail: "Not found", code: "website_page_not_found" });
		if (one[2] && method === "POST") {
			log();
			return done(202, pageOut(found));
		}
		if (!one[2] && method === "PATCH") {
			if (notOwner) return ownerErr();
			log();
			const excluded = body.excluded === true;
			found.excluded_by_owner = excluded;
			found.status = excluded ? "excluded" : "queued";
			found.skip_reason = excluded ? "owner" : null;
			return done(200, pageOut(found));
		}
	}
	return false;
}
