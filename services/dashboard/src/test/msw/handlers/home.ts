import { HttpResponse, http } from "msw";
import type { Home } from "@/features/home/api";
import type { Article, KnowledgeDoc } from "@/features/knowledge/api";
import { BASE, CORPORA } from "../data";
import { coded } from "./auth";

/**
 * BIZ3 handlers: `GET /home`, the articles routes and the `?source=` filter of `GET /documents`,
 * stateful within a test (reset in setup.ts). Shree Dental Clinic, Pune is the demo business.
 */
export const PATIENT = CORPORA[0]?.id ?? "";
export const STAFF = CORPORA[1]?.id ?? "";

const NOW = "2026-10-05T08:00:00Z";
const nid = (n: number) => `a2710000-0000-4000-8000-${String(n).padStart(12, "0")}`;

export function homeFor(kb: string | null): Home {
	return {
		kb_id: kb,
		kb_name: kb ? (CORPORA.find((c) => c.id === kb)?.name ?? "Patient help") : null,
		assistant: kb
			? {
					enabled: true,
					published: true,
					name: "Shree Dental Assistant",
					avatar_url: null,
					business_name: "Shree Dental Clinic, Pune",
				}
			: { enabled: false, published: false, name: null, avatar_url: null, business_name: null },
		website: kb
			? {
					learning: true,
					connected: false, // BIZ4
					sites: [
						{
							origin: "https://shreedental.in",
							status: "ready",
							pages_learned: 12,
							last_updated_at: new Date(Date.now() - 2 * 3_600_000).toISOString(),
						},
					],
				}
			: { learning: false, connected: false, sites: [] }, // BIZ4
		today: { date: "2026-10-05", timezone: "Asia/Kolkata", conversations: 3, leads: 1, handoffs_open: 2 },
	};
}

export const homeState = {
	/** Per knowledge base overrides; a test sets what it needs. */
	over: {} as Record<string, Partial<Home>>,
	requests: [] as (string | null)[],
};

interface Row {
	article: Article;
	doc: KnowledgeDoc | null;
}

interface ArticlesState {
	rows: Row[];
	files: KnowledgeDoc[];
	/** Hold new documents back, as if the assistant were still learning them. */
	held: boolean;
	sent: { method: string; kb: string; id?: string; body: unknown }[];
	limitReached: boolean;
	deleted: string[];
}

function seedFiles(): KnowledgeDoc[] {
	return [
		{
			id: "f11e0001-0000-4000-8000-000000000001",
			file_name: "fees-brochure-2026.pdf",
			mime_type: "application/pdf",
			size_bytes: 120_000,
			ingested_at: "2026-10-01T10:00:00Z",
			external_id: null,
			metadata: null,
			active_version_id: null,
			version_count: 1,
		},
	];
}

export const articlesState: ArticlesState = {
	rows: [],
	files: seedFiles(),
	held: false,
	sent: [],
	limitReached: false,
	deleted: [],
};
let seq = 0;

export function resetHomeState(): void {
	homeState.over = {};
	homeState.requests = [];
	articlesState.rows = [];
	articlesState.files = seedFiles();
	articlesState.held = false;
	articlesState.sent = [];
	articlesState.limitReached = false;
	articlesState.deleted = [];
	seq = 0;
}

function docFor(a: Article, kind: "article" | "faq"): KnowledgeDoc {
	return {
		id: nid(900 + seq),
		file_name: a.title,
		mime_type: "text/plain",
		size_bytes: a.body?.length ?? 0,
		ingested_at: NOW,
		external_id: `${kind}:${a.id}`,
		metadata: { source: "articles", kind },
		active_version_id: nid(950 + seq),
		version_count: 1,
	};
}

/** Add one article straight to the list (a test's starting point). */
export function seedArticle(kind: "article" | "faq", title: string, body: string, kb = PATIENT): Article {
	seq += 1;
	const article: Article = {
		id: nid(seq),
		document_id: null,
		kind,
		title,
		body,
		status: "ready",
		job_id: null,
		created_by: "priya@shree-labs.in",
		created_at: NOW,
		updated_at: NOW,
	};
	const doc = docFor(article, kind);
	article.document_id = doc.id;
	articlesState.rows.push({ article, doc });
	void kb;
	return article;
}

/** Let the held documents appear (the assistant finished learning). */
export function releaseHeld(): void {
	articlesState.held = false;
	for (const r of articlesState.rows) r.doc ??= docFor(r.article, r.article.kind);
	for (const r of articlesState.rows) if (r.doc) r.article.document_id = r.doc.id;
}

const find = (id: string) => articlesState.rows.find((r) => r.article.id === id || r.doc?.id === id);

export const homeHandlers = [
	http.get(`${BASE}/api/v1/home`, ({ request }) => {
		const kb = new URL(request.url).searchParams.get("corpus_id");
		homeState.requests.push(kb);
		const base = homeFor(kb ?? PATIENT);
		return HttpResponse.json({ ...base, ...(homeState.over[kb ?? PATIENT] ?? {}) });
	}),
	// `?source=` narrows the documents list; any other request falls through to the documents handlers.
	http.get(`${BASE}/api/v1/documents`, ({ request }) => {
		const source = new URL(request.url).searchParams.get("source");
		if (!source) return undefined;
		const items =
			source === "files"
				? articlesState.files
				: source === "articles"
					? articlesState.rows.flatMap((r) => (r.doc ? [r.doc] : []))
					: [];
		return HttpResponse.json({ items, count: items.length });
	}),
	// Deleting a document the BIZ3 lists know about; any other document falls through.
	http.delete(`${BASE}/api/v1/documents/:id`, ({ params }) => {
		const id = String(params.id);
		const row = articlesState.rows.find((r) => r.doc?.id === id);
		const file = articlesState.files.find((f) => f.id === id);
		if (!row && !file) return undefined;
		articlesState.deleted.push(id);
		articlesState.rows = articlesState.rows.filter((r) => r !== row);
		articlesState.files = articlesState.files.filter((f) => f !== file);
		return new HttpResponse(null, { status: 204 });
	}),
	http.post(`${BASE}/api/v1/corpora/:kb/articles`, async ({ params, request }) => {
		const body = (await request.json()) as { kind: "article" | "faq"; title: string; body: string };
		articlesState.sent.push({ method: "POST", kb: String(params.kb), body });
		if (articlesState.limitReached) {
			return coded(409, "document_limit_reached", { limit: 50, used: 50, adding: 1 });
		}
		seq += 1;
		const article: Article = {
			id: nid(seq),
			document_id: null,
			kind: body.kind,
			title: body.title,
			body: body.body,
			status: "queued",
			job_id: nid(500 + seq),
			created_by: "priya@shree-labs.in",
			created_at: NOW,
			updated_at: NOW,
		};
		articlesState.rows.push({ article, doc: articlesState.held ? null : docFor(article, body.kind) });
		const row = articlesState.rows[articlesState.rows.length - 1];
		if (row?.doc) article.document_id = row.doc.id;
		return HttpResponse.json(article, { status: 202 });
	}),
	http.get(`${BASE}/api/v1/corpora/:kb/articles/:id`, ({ params }) => {
		const row = find(String(params.id));
		if (!row) return coded(404, "article_not_found");
		return HttpResponse.json({ ...row.article, status: row.doc ? "ready" : "queued" });
	}),
	http.put(`${BASE}/api/v1/corpora/:kb/articles/:id`, async ({ params, request }) => {
		const body = (await request.json()) as { kind: "article" | "faq"; title: string; body: string };
		articlesState.sent.push({ method: "PUT", kb: String(params.kb), id: String(params.id), body });
		const row = find(String(params.id));
		if (!row) return coded(404, "article_not_found");
		if (body.kind !== row.article.kind) return coded(422, "article_kind_changed");
		row.article.title = body.title;
		row.article.body = body.body;
		if (row.doc) row.doc.file_name = body.title;
		return HttpResponse.json({ ...row.article, job_id: nid(700 + seq) }, { status: 202 });
	}),
];
