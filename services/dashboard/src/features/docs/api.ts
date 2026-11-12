import { keepPreviousData, queryOptions, useQuery } from "@tanstack/react-query";
import { authFetch } from "@/lib/api/auth-fetch";
import { parseApiError, toApiError } from "@/lib/api/errors";
import { currentRagUrl } from "@/lib/auth/store";
import type { PageMeta } from "@/lib/nav";

/**
 * The Docs API (COB8): `GET /api/v1/docs/tree`, `/pages/{slug}` and `/search`. Session-only, so
 * only people ever send these; the engine filters every answer to the person's role. Types are
 * written here, matching `api/schemas/docs.py`.
 */
export interface DocsTreePage {
	slug: string;
	title: string;
	route: string | null;
	summary: string;
}
export interface DocsTopic {
	id: string;
	title: string;
	pages: DocsTreePage[];
}
export interface DocsTree {
	version: string;
	topics: DocsTopic[];
}
export interface DocsTocItem {
	anchor: string;
	title: string;
	level: number;
}
export interface DocsNeighbor {
	slug: string;
	title: string;
}
export interface DocPage {
	slug: string;
	title: string;
	topic: string;
	route: string | null;
	summary: string;
	markdown: string;
	toc: DocsTocItem[];
	version: string;
	public_url: string | null;
	prev: DocsNeighbor | null;
	next: DocsNeighbor | null;
}
export interface DocsSearchItem {
	slug: string;
	anchor: string;
	title: string;
	snippet: string;
	route: string | null;
	score: number;
}

async function getJson<T>(path: string, query?: Record<string, string | number>): Promise<T> {
	const url = new URL(`${currentRagUrl()}${path}`);
	for (const [k, v] of Object.entries(query ?? {})) url.searchParams.set(k, String(v));
	let res: Response;
	try {
		res = await authFetch(url);
	} catch (e) {
		throw toApiError(e);
	}
	if (!res.ok) throw await parseApiError(res);
	return (await res.json()) as T;
}

export const docsKeys = {
	all: ["docs"] as const,
	tree: ["docs", "tree"] as const,
	page: (slug: string) => ["docs", "page", slug] as const,
	search: (q: string) => ["docs", "search", q] as const,
};

/** The pages never change inside a session of one engine build, so they are kept for a while. */
const STALE = 10 * 60_000;

export const docsTreeQuery = queryOptions({
	queryKey: docsKeys.tree,
	queryFn: () => getJson<DocsTree>("/api/v1/docs/tree"),
	staleTime: STALE,
	retry: false,
});

export function docsPageQuery(slug: string) {
	return queryOptions({
		queryKey: docsKeys.page(slug),
		queryFn: () => getJson<DocPage>(`/api/v1/docs/pages/${slug.split("/").map(encodeURIComponent).join("/")}`),
		staleTime: STALE,
		retry: false,
	});
}

export const SEARCH_MIN = 2;
export const SEARCH_MAX = 200;

/** Search runs on the engine (free: never a COB question). Short queries do not search. */
export function useDocsSearch(q: string) {
	const query = q.trim().slice(0, SEARCH_MAX);
	return useQuery({
		queryKey: docsKeys.search(query),
		queryFn: () => getJson<{ items: DocsSearchItem[] }>("/api/v1/docs/search", { q: query, limit: 12 }),
		enabled: query.length >= SEARCH_MIN,
		staleTime: 60_000,
		retry: false,
		placeholderData: keepPreviousData,
	});
}

/** The pages whose title holds the query: the fallback when the engine cannot search. */
export function filterTitles(tree: DocsTree | undefined, q: string): DocsSearchItem[] {
	const needle = q.trim().toLowerCase();
	if (!tree || !needle) return [];
	const out: DocsSearchItem[] = [];
	for (const topic of tree.topics) {
		for (const p of topic.pages) {
			if (p.title.toLowerCase().includes(needle)) {
				out.push({ slug: p.slug, anchor: "", title: p.title, snippet: p.summary, route: p.route, score: 1 });
			}
		}
	}
	return out;
}

/** The placeholder in the engine's curl examples: the engine address this dashboard talks to. */
export const BASE_URL_TOKEN = "{{BASE_URL}}";

export function fillBaseUrl(markdown: string, baseUrl: string): string {
	return markdown.split(BASE_URL_TOKEN).join(baseUrl.replace(/\/+$/, ""));
}

const PARAM = /^\$[A-Za-z_]\w*$/;

function templateMatches(template: string, path: string): boolean {
	const t = template.split("/");
	const p = path.split("/");
	for (let i = 0; i < t.length; i++) {
		const seg = t[i] ?? "";
		if (seg === "$" && i === t.length - 1) return p.length >= i;
		if (p[i] === undefined) return false;
		if (!PARAM.test(seg) && seg !== p[i]) return false;
	}
	return t.length === p.length;
}

function cleanPath(path: string): string {
	const base = (path.split("?")[0] ?? "").split("#")[0] ?? "";
	return base.length > 1 ? base.replace(/\/+$/, "") : base || "/";
}

/**
 * The screen's help page for a dashboard path, as the engine's `DocsIndex.page_for_route` finds
 * it: exact, then the template with the most fixed segments (`/kbs/$kbId`), then the nearest
 * parent path.
 */
export function pageForRoute(tree: DocsTree | undefined, route: string): DocsTreePage | null {
	if (!tree) return null;
	const pages = tree.topics.flatMap((t) => t.pages).filter((p) => p.route);
	let path = cleanPath(route);
	for (;;) {
		const exact = pages.find((p) => p.route === path);
		if (exact) return exact;
		let best: DocsTreePage | null = null;
		let bestFixed = -1;
		for (const p of pages) {
			const template = p.route ?? "";
			if (!template.includes("$") || !templateMatches(template, path)) continue;
			const fixed = template.split("/").filter((s) => s && !s.startsWith("$")).length;
			if (fixed > bestFixed) {
				best = p;
				bestFixed = fixed;
			}
		}
		if (best) return best;
		if (path === "/") return null;
		path = path.slice(0, path.lastIndexOf("/")) || "/";
	}
}

/**
 * Where "Open this screen" goes: the page's route when it is one of the person's own screens
 * (`nav`), with `$kbId` filled by the selected knowledge base; a route with other parameters
 * (a job, a document, a customer) goes to its list screen. Null for screens outside the nav
 * (sign-in pages, operator screens for a customer).
 */
export function screenTarget(route: string | null, nav: readonly PageMeta[], kbId: string | null): string | null {
	if (!route) return null;
	const segments = route.split("/");
	const fixed: string[] = [];
	for (const s of segments) {
		if (s.startsWith("$")) break;
		fixed.push(s);
	}
	const list = fixed.join("/") || "/";
	if (!nav.some((p) => p.path === list)) return null;
	if (segments.every((s) => !s.startsWith("$"))) return route;
	if (route === `/kbs/$kbId` && kbId) return `/kbs/${kbId}`;
	if (route === `/kbs/$kbId/setup` && kbId) return `/kbs/${kbId}/setup`;
	return list;
}
