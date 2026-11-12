import { randomUUID } from "node:crypto";
import type { BrowserContext, Route } from "@playwright/test";
import { MOCK_ENGINE, type World } from "./mock-engine";

/**
 * Insights and Quality part of the fake engine (D9), installed by the spec on top of
 * `installMockEngine` (a later `context.route` runs first; anything else falls back to the
 * engine), so the shared mock-engine index stays untouched. It follows the real routes:
 * api/routes/analytics.py, gaps.py (queued → completed; a tenant must name a corpus), feedback.py,
 * golden.py (import skips rows without a question or answer; from-feedback needs a correction;
 * delete owner-only, A8), memories.py (bulk delete and purge owner-only, A5) and admin.py (eval,
 * optimise and global gaps admin-only). Question ids are shared with the query mock so
 * `GET /query/{id}` answers for them. Indian demo data only.
 */

const PATIENT = "7b221003-0000-4000-8000-000000000001";

const CORS = {
	"access-control-allow-origin": "*",
	"access-control-allow-headers": "authorization, x-api-key, content-type",
	"access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
};

interface Feedback {
	id: string;
	query_id: string;
	corpus_id: string;
	question: string;
	rating: number;
	correction: string | null;
	feedback_type: string;
	session_id: string | null;
	created_at: string;
}
interface Pair {
	id: string;
	corpus_id: string;
	query: string;
	expected_answer: string;
	expected_chunk_ids: string[] | null;
	metadata: Record<string, unknown> | null;
	source: string;
	created_at: string;
}
interface Gap {
	id: string;
	corpus_id: string | null;
	status: string;
	query_count: number;
	cluster_count: number;
	started_at: string;
	completed_at: string | null;
	clusters: { topic_id: number; query_count: number; keywords: string[]; representative_queries: string[] }[] | null;
	reads: number;
}
interface Run {
	id: string;
	status: string;
	corpus_id: string | null;
	sample_size: number | null;
	n_trials: number | null;
	created_at: string;
	completed_at: string | null;
}

export interface InsightsState {
	feedback: Feedback[];
	golden: Pair[];
	gaps: Gap[];
	memories: Record<string, unknown>[];
	evals: Run[];
	optimize: Run[];
}

const SEED: [string, string, number | null, number][] = [
	[
		"Do I need to fast for a lipid profile?",
		"Yes. Fast for 10–12 hours before a lipid profile; water is fine.",
		0.93,
		3,
	],
	["लिपिड प्रोफाइल के लिए उपवास ज़रूरी है?", "हाँ, 10–12 घंटे उपवास करें; पानी पी सकते हैं।", 0.88, 5],
	["रविवारी घरून नमुना घेता का?", "मला कागदपत्रांमध्ये याची माहिती मिळाली नाही.", 0.41, 26],
	["Is HbA1c included in the diabetes package?", "The documents don't say which tests the package includes.", 0.36, 50],
];

/** Seeds the Patient help questions into the query mock (so `/query/{id}` answers them). */
export function createInsightsState(world: World): InsightsState {
	const now = Date.now();
	for (const [question, answer, confidence, hoursAgo] of SEED) {
		const id = randomUUID();
		world.query.queries.set(id, {
			id,
			corpusId: PATIENT,
			question,
			answer,
			model: "openai/gpt-oss-20b",
			latency: 900 + hoursAgo * 20,
			usage: { prompt_tokens: 1100, completion_tokens: 140, total_tokens: 1240 },
			confidence,
			// Not part of QueryRecord; used for ordering the history below.
			createdAt: new Date(now - hoursAgo * 3_600_000).toISOString(),
		} as Parameters<World["query"]["queries"]["set"]>[1]);
	}
	return {
		feedback: [],
		golden: [
			{
				id: randomUUID(),
				corpus_id: PATIENT,
				query: "Do I need to fast for a lipid profile?",
				expected_answer: "Yes, 10–12 hours; water is fine.",
				expected_chunk_ids: null,
				metadata: null,
				source: "manual",
				created_at: new Date(now - 86_400_000).toISOString(),
			},
		],
		gaps: [
			{
				id: randomUUID(),
				corpus_id: PATIENT,
				status: "completed",
				query_count: 14,
				cluster_count: 1,
				started_at: new Date(now - 2 * 86_400_000).toISOString(),
				completed_at: new Date(now - 2 * 86_400_000 + 180_000).toISOString(),
				clusters: [
					{
						topic_id: 0,
						query_count: 7,
						keywords: ["home collection", "baner", "sunday"],
						representative_queries: ["Home collection in Baner on Sunday?", "रविवारी घरून नमुना घेता का?"],
					},
				],
				reads: 99,
			},
		],
		memories: [
			{
				id: randomUUID(),
				corpus_id: PATIENT,
				user_id: "visitor-17",
				memory_type: "preference",
				content: "Prefers home collection in Kothrud before 9 am",
				category: "visits",
				related_entity: "home collection",
				confidence: 1,
				source: "explicit",
				metadata: null,
				created_at: new Date(now - 3 * 86_400_000).toISOString(),
			},
		],
		evals: [],
		optimize: [],
	};
}

type Who = { admin: boolean; tenantId: string | null; role: "owner" | "member" | null } | null;

function whoIs(w: World, headers: Record<string, string>): Who {
	const key = headers["x-api-key"];
	if (key) {
		const k = w.keys.get(key);
		if (!k) return null;
		return { admin: k.kind === "admin", tenantId: k.tenantId ?? null, role: k.kind === "tenant" ? "owner" : null };
	}
	const auth = headers.authorization;
	if (!auth?.toLowerCase().startsWith("bearer ")) return null;
	const a = w.access.get(auth.slice(7));
	if (!a || a.expired) return null;
	const s = w.sessions.get(a.sid);
	const u = s ? w.users.find((x) => x.id === s.userId) : undefined;
	if (!s || s.revoked || !u) return null;
	return {
		admin: u.superadmin,
		tenantId: s.tenantId,
		role: u.memberships.find((m) => m.tenantId === s.tenantId)?.role ?? null,
	};
}

/** The text of the multipart part carrying a file, and its file name. */
function multipartFile(body: Buffer | null, contentType: string): { name: string; text: string } | null {
	const boundary = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType);
	if (!body || !boundary) return null;
	const sep = Buffer.from(`--${boundary[1] ?? boundary[2]}`);
	let pos = body.indexOf(sep);
	while (pos !== -1) {
		const next = body.indexOf(sep, pos + sep.length);
		if (next === -1) break;
		const part = body.subarray(pos + sep.length + 2, next - 2);
		const headEnd = part.indexOf("\r\n\r\n");
		if (headEnd !== -1) {
			const head = part.subarray(0, headEnd).toString("utf8");
			const filename = /filename="([^"]*)"/.exec(head)?.[1];
			if (filename !== undefined) return { name: filename, text: part.subarray(headEnd + 4).toString("utf8") };
		}
		pos = next;
	}
	return null;
}

/** Minimal RFC 4180 rows (quoted cells, doubled quotes, CRLF). */
function csvRows(text: string): string[][] {
	const rows: string[][] = [];
	let row: string[] = [];
	let cell = "";
	let quoted = false;
	for (let i = 0; i < text.length; i++) {
		const ch = text[i];
		if (quoted) {
			if (ch === '"' && text[i + 1] === '"') {
				cell += '"';
				i++;
			} else if (ch === '"') quoted = false;
			else cell += ch;
		} else if (ch === '"') quoted = true;
		else if (ch === ",") {
			row.push(cell);
			cell = "";
		} else if (ch === "\n" || ch === "\r") {
			if (ch === "\r" && text[i + 1] === "\n") i++;
			row.push(cell);
			if (row.some((c) => c !== "")) rows.push(row);
			row = [];
			cell = "";
		} else cell += ch;
	}
	row.push(cell);
	if (row.some((c) => c !== "")) rows.push(row);
	return rows;
}

export async function installInsightsMock(context: BrowserContext, world: World, s: InsightsState): Promise<void> {
	await context.route(`${MOCK_ENGINE}/api/v1/**`, async (route: Route) => {
		const req = route.request();
		const method = req.method();
		const url = new URL(req.url());
		const path = url.pathname;
		if (method === "OPTIONS") return route.fallback();
		const mine =
			/^\/api\/v1\/(analytics\/|feedback|golden|admin\/(eval|optimize|gaps)|query\/[^/]+$)/.test(path) ||
			/^\/api\/v1\/corpora\/[^/]+\/memories/.test(path);
		if (!mine || /^\/api\/v1\/query\/(stream|retrieve)$/.test(path)) return route.fallback();
		const who = whoIs(world, await req.allHeaders());
		// Unauthenticated, expired or revoked: the engine mock answers (401 codes, refresh).
		if (!who) return route.fallback();
		// A GET /query/{id} is answered by the query mock (its records carry the seeded questions).
		if (/^\/api\/v1\/query\//.test(path)) return route.fallback();

		const json = (status: number, body?: unknown) =>
			route.fulfill({
				status,
				headers: { ...CORS, "content-type": "application/json" },
				body: body === undefined ? "" : JSON.stringify(body),
			});
		const q = (k: string) => url.searchParams.get(k);
		const body = (() => {
			try {
				return (req.postDataJSON() as Record<string, unknown>) ?? {};
			} catch {
				return {};
			}
		})();
		const kbOf = (id: string | null | undefined) => world.kbs.items.find((k) => k.id === id);
		const visibleKbs = world.kbs.items.filter((k) => who.admin || k.tenant_id === who.tenantId).map((k) => k.id);
		const canUse = (id: string | null | undefined) => !!id && visibleKbs.includes(id);
		const page = <T>(items: T[], def = 20) => {
			const limit = Number(q("limit") ?? def);
			const offset = Number(q("offset") ?? 0);
			return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
		};
		const scope = (): string[] | "forbidden" => {
			const c = q("corpus_id");
			if (c) return canUse(c) ? [c] : "forbidden";
			return visibleKbs;
		};
		const ownerOnly = () => who.role === "member";
		const adminOnly = () => !who.admin;

		// ---- analytics ----
		const records = [...world.query.queries.values()].map((r) => {
			const x = r as typeof r & { createdAt?: string };
			return {
				id: r.id,
				corpus_id: r.corpusId,
				query_text: r.question,
				response: r.answer,
				confidence_score: r.confidence,
				latency_ms: r.latency,
				session_id: null,
				created_at: x.createdAt ?? new Date().toISOString(),
			};
		});
		if (method === "GET" && path.startsWith("/api/v1/analytics/queries/")) {
			const sc = scope();
			if (sc === "forbidden") return json(403, { detail: "Corpus does not belong to this tenant." });
			const rows = records
				.filter((r) => sc.includes(r.corpus_id))
				.sort((a, b) => b.created_at.localeCompare(a.created_at));
			if (path.endsWith("/history")) return json(200, page(rows));
			if (path.endsWith("/count")) return json(200, { count: rows.length });
			if (path.endsWith("/confidence")) {
				const b = [0, 0, 0, 0, 0];
				for (const r of rows) {
					if (r.confidence_score == null) continue;
					const i = Math.min(4, Math.floor(r.confidence_score * 5));
					b[i] = (b[i] ?? 0) + 1;
				}
				return json(200, {
					buckets: ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"].map((range, i) => ({ range, count: b[i] })),
				});
			}
			if (path.endsWith("/trends")) {
				const byDay = new Map<string, { count: number; conf: number[]; lat: number[] }>();
				for (const r of rows) {
					const day = `${r.created_at.slice(0, 10)}T00:00:00+00:00`;
					const e = byDay.get(day) ?? { count: 0, conf: [], lat: [] };
					e.count++;
					if (r.confidence_score != null) e.conf.push(r.confidence_score);
					e.lat.push(r.latency_ms);
					byDay.set(day, e);
				}
				const avg = (a: number[]) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
				return json(200, {
					data: [...byDay.entries()]
						.sort(([a], [b]) => a.localeCompare(b))
						.map(([bucket, e]) => ({ bucket, count: e.count, avg_confidence: avg(e.conf), avg_latency: avg(e.lat) })),
				});
			}
		}

		// ---- gaps ----
		const gapOut = (g: Gap) => {
			const { reads: _r, ...rest } = g;
			return rest;
		};
		const finish = (g: Gap) => {
			g.reads++;
			if (g.status !== "completed" && g.reads >= 2) {
				g.status = "completed";
				g.query_count = 3;
				g.cluster_count = 1;
				g.completed_at = new Date().toISOString();
				g.clusters = [
					{
						topic_id: 0,
						query_count: 3,
						keywords: ["hba1c", "diabetes", "package"],
						representative_queries: ["Is HbA1c included in the diabetes package?"],
					},
				];
			}
		};
		const newGap = (corpus: string | null): Gap => {
			const g: Gap = {
				id: randomUUID(),
				corpus_id: corpus,
				status: "queued",
				query_count: 0,
				cluster_count: 0,
				started_at: new Date().toISOString(),
				completed_at: null,
				clusters: null,
				reads: 0,
			};
			s.gaps.unshift(g);
			return g;
		};
		if (path === "/api/v1/analytics/gaps" && method === "GET") {
			const c = q("corpus_id");
			if (c && !canUse(c)) return json(403, { detail: "Corpus does not belong to this tenant." });
			const rows = s.gaps.filter((g) =>
				c ? g.corpus_id === c : who.admin ? true : g.corpus_id !== null && visibleKbs.includes(g.corpus_id),
			);
			const p = page(rows);
			return json(200, { items: p.items.map(gapOut), total: p.total });
		}
		if (path === "/api/v1/analytics/gaps/run" && method === "POST") {
			const c = q("corpus_id");
			if (!c && !who.admin) return json(400, { detail: "corpus_id is required unless authenticated as admin." });
			if (c && !canUse(c)) return json(403, { detail: "Corpus is outside this key's scope." });
			return json(202, gapOut(newGap(c)));
		}
		const gapId = /^\/api\/v1\/analytics\/gaps\/([^/]+)$/.exec(path)?.[1];
		if (gapId && method === "GET") {
			const g = s.gaps.find((x) => x.id === gapId);
			if (!g) return json(404, { detail: `Gap report ${gapId} not found` });
			if (!who.admin && !(g.corpus_id && canUse(g.corpus_id)))
				return json(403, { detail: "Gap report is outside this key's scope." });
			finish(g);
			return json(200, gapOut(g));
		}
		if (path === "/api/v1/admin/gaps" && method === "GET") {
			if (adminOnly()) return json(403, { detail: "Admin access required" });
			const g = s.gaps[0];
			if (!g) return json(404, { detail: "No gap reports available" });
			return json(200, {
				id: g.id,
				corpus_id: g.corpus_id,
				status: g.status,
				created_at: g.started_at,
				topics: g.clusters ?? [],
				total_queries_analyzed: g.query_count,
				low_confidence_count: 0,
			});
		}
		if (path === "/api/v1/admin/gaps/trigger" && method === "POST") {
			if (adminOnly()) return json(403, { detail: "Admin access required" });
			return json(202, { gap_report_id: newGap(q("corpus_id")).id, status: "queued" });
		}

		// ---- feedback ----
		if (path === "/api/v1/feedback" && method === "GET") {
			const sc = scope();
			if (sc === "forbidden") return json(403, { detail: "Corpus does not belong to this tenant." });
			const min = Number(q("min_rating") ?? 1);
			const max = Number(q("max_rating") ?? 5);
			return json(200, page(s.feedback.filter((f) => sc.includes(f.corpus_id) && f.rating >= min && f.rating <= max)));
		}
		if (path === "/api/v1/feedback" && method === "POST") {
			const rec = world.query.queries.get(String(body.query_id ?? ""));
			if (!rec) return json(404, { detail: `Query ${String(body.query_id)} not found` });
			if (!canUse(rec.corpusId)) return json(403, { detail: "Query does not belong to this corpus" });
			const f: Feedback = {
				id: randomUUID(),
				query_id: rec.id,
				corpus_id: rec.corpusId,
				question: rec.question,
				rating: Number(body.rating),
				correction: typeof body.correction === "string" ? body.correction : null,
				feedback_type: "explicit",
				session_id: null,
				created_at: new Date().toISOString(),
			};
			s.feedback.unshift(f);
			world.query.feedback.push({
				query_id: rec.id,
				rating: f.rating,
				...(f.correction ? { correction: f.correction } : {}),
			});
			return json(201, { feedback_id: f.id, message: "Feedback recorded successfully" });
		}
		if (path === "/api/v1/feedback/stats" && method === "GET") {
			const c = q("corpus_id");
			if (!c) return json(422, { detail: [{ loc: ["query", "corpus_id"], msg: "Field required", type: "missing" }] });
			if (!canUse(c)) return json(403, { detail: "Corpus is outside this key's scope." });
			const rows = s.feedback.filter((f) => f.corpus_id === c);
			return json(200, {
				corpus_id: c,
				total: rows.length,
				positive: rows.filter((f) => f.rating >= 4).length,
				negative: rows.filter((f) => f.rating <= 2).length,
				corrections: rows.filter((f) => f.correction).length,
				implicit: 0,
				start: new Date(Date.now() - 30 * 86_400_000).toISOString(),
				end: new Date().toISOString(),
			});
		}

		// ---- golden ----
		const addPair = (p: Omit<Pair, "id" | "created_at">) => {
			const pair: Pair = { ...p, id: randomUUID(), created_at: new Date().toISOString() };
			s.golden.unshift(pair);
			return pair;
		};
		if (path === "/api/v1/golden" && method === "GET") {
			const c = q("corpus_id");
			if (!canUse(c)) return json(403, { detail: "Corpus is outside this key's scope." });
			const p = page(
				s.golden.filter((x) => x.corpus_id === c),
				50,
			);
			return json(200, { ...p, count: p.items.length });
		}
		if (path === "/api/v1/golden" && method === "POST") {
			if (!canUse(String(body.corpus_id))) return json(403, { detail: "Corpus is outside this key's scope." });
			return json(
				201,
				addPair({
					corpus_id: String(body.corpus_id),
					query: String(body.query),
					expected_answer: String(body.expected_answer),
					expected_chunk_ids: (body.expected_chunk_ids as string[] | null) ?? null,
					metadata: null,
					source: "manual",
				}),
			);
		}
		if (path === "/api/v1/golden/export" && method === "GET") {
			const c = q("corpus_id");
			if (!canUse(c)) return json(403, { detail: "Corpus is outside this key's scope." });
			const items = s.golden.filter((x) => x.corpus_id === c);
			return json(200, { items, count: items.length });
		}
		if (path === "/api/v1/golden/import" && method === "POST") {
			const c = q("corpus_id");
			if (!canUse(c)) return json(403, { detail: "Corpus is outside this key's scope." });
			const file = multipartFile(req.postDataBuffer(), (await req.allHeaders())["content-type"] ?? "");
			if (!file?.name.endsWith(".csv")) return json(400, { detail: "File must be a .csv" });
			const [header = [], ...rows] = csvRows(file.text);
			const qi = header.indexOf("query");
			const ai = header.indexOf("expected_answer");
			let n = 0;
			for (const r of rows) {
				const query = (r[qi] ?? "").trim();
				const answer = (r[ai] ?? "").trim();
				if (qi < 0 || ai < 0 || !query || !answer) continue;
				addPair({
					corpus_id: String(c),
					query,
					expected_answer: answer,
					expected_chunk_ids: null,
					metadata: null,
					source: "import",
				});
				n++;
			}
			return json(201, { imported_count: n, message: "Import complete" });
		}
		if (path === "/api/v1/golden/from-feedback" && method === "POST") {
			const f = s.feedback.find((x) => x.id === body.feedback_id);
			if (!f) return json(404, { detail: "Feedback not found" });
			if (!canUse(f.corpus_id)) return json(403, { detail: "Corpus is outside this key's scope." });
			if (!f.correction) return json(400, { detail: `Feedback ${f.id} has no correction text` });
			return json(
				201,
				addPair({
					corpus_id: f.corpus_id,
					query: f.question,
					expected_answer: f.correction,
					expected_chunk_ids: null,
					metadata: { feedback_id: f.id, original_rating: f.rating },
					source: "feedback",
				}),
			);
		}
		const pairId = /^\/api\/v1\/golden\/([^/]+)$/.exec(path)?.[1];
		if (pairId) {
			const p = s.golden.find((x) => x.id === pairId);
			if (!p) return json(404, { detail: `Golden pair ${pairId} not found` });
			if (method === "DELETE" && ownerOnly())
				return json(403, { detail: "Only a workspace owner can do this", code: "owner_required" });
			if (!canUse(p.corpus_id)) return json(403, { detail: "Corpus is outside this key's scope." });
			if (method === "PATCH") {
				Object.assign(p, body);
				return json(200, p);
			}
			if (method === "DELETE") {
				s.golden.splice(s.golden.indexOf(p), 1);
				return json(200, { message: "Golden pair deleted successfully" });
			}
		}

		// ---- memories ----
		const mem = /^\/api\/v1\/corpora\/([^/]+)\/memories(?:\/([^/]+))?$/.exec(path);
		if (mem) {
			const corpus = mem[1] ?? "";
			const sub = mem[2];
			if (!kbOf(corpus)) return json(404, { detail: "Corpus not found" });
			if (!canUse(corpus)) return json(403, { detail: "Corpus does not belong to this tenant." });
			const mine2 = s.memories.filter((m) => m.corpus_id === corpus);
			const matches = (m: Record<string, unknown>) =>
				(!q("memory_type") || m.memory_type === q("memory_type")) && (!q("user_id") || m.user_id === q("user_id"));
			const make = (b: Record<string, unknown>) => ({
				id: randomUUID(),
				corpus_id: corpus,
				user_id: String(b.user_id),
				memory_type: String(b.memory_type ?? "fact"),
				content: String(b.content),
				category: b.category ?? null,
				related_entity: b.related_entity ?? null,
				confidence: Number(b.confidence ?? 1),
				source: String(b.source ?? "explicit"),
				metadata: null,
				created_at: new Date().toISOString(),
			});
			if (!sub && method === "GET") return json(200, page(mine2.filter(matches), 50));
			if (!sub && method === "POST") {
				const m = make(body);
				s.memories.unshift(m);
				return json(201, m);
			}
			if (!sub && method === "DELETE") {
				if (ownerOnly()) return json(403, { detail: "Only a workspace owner can do this", code: "owner_required" });
				const gone = mine2.filter(matches);
				s.memories = s.memories.filter((m) => !gone.includes(m));
				return json(200, { deleted: gone.length });
			}
			if (sub === "purge" && method === "DELETE") {
				if (ownerOnly()) return json(403, { detail: "Only a workspace owner can do this", code: "owner_required" });
				return json(200, { deleted: 0 });
			}
			if (sub === "bulk" && method === "POST") {
				const created = ((body.memories as Record<string, unknown>[]) ?? []).map(make);
				s.memories.unshift(...created);
				return json(201, created);
			}
			if (sub === "search" && method === "POST") {
				const term = String(body.query ?? "").toLowerCase();
				const items = mine2.filter((m) => m.user_id === body.user_id && String(m.content).toLowerCase().includes(term));
				return json(200, { items, query: body.query, total: items.length });
			}
			if (sub) {
				const m = mine2.find((x) => x.id === sub);
				if (!m) return json(404, { detail: "Memory not found" });
				if (method === "GET") return json(200, m);
				if (method === "DELETE") {
					s.memories.splice(s.memories.indexOf(m), 1);
					return route.fulfill({ status: 204, headers: CORS });
				}
			}
		}

		// ---- evaluation and optimisation (admin) ----
		const runs = /^\/api\/v1\/admin\/(eval|optimize)\/(runs|trigger)(?:\/([^/]+))?$/.exec(path);
		if (runs) {
			if (adminOnly()) return json(403, { detail: "Admin access required" });
			const list = runs[1] === "eval" ? s.evals : s.optimize;
			if (runs[2] === "trigger" && method === "POST") {
				const r: Run = {
					id: randomUUID(),
					status: "queued",
					corpus_id: (body.corpus_id as string | null) ?? null,
					sample_size: (body.sample_size as number | null) ?? null,
					n_trials: (body.n_trials as number | null) ?? null,
					created_at: new Date().toISOString(),
					completed_at: null,
				};
				list.unshift(r);
				return json(
					202,
					runs[1] === "eval"
						? { eval_run_id: r.id, status: "queued" }
						: { optimization_run_id: r.id, status: "queued" },
				);
			}
			if (runs[3]) {
				const r = list.find((x) => x.id === runs[3]);
				if (!r) return json(404, { detail: "Run not found" });
				// A queued run stays queued here (no worker in the mock): the spec checks "queued".
				return json(
					200,
					runs[1] === "eval"
						? { ...r, error_message: null, metrics: {}, config_snapshot: {} }
						: { ...r, error_message: null, pareto_configs: [], best_config: {} },
				);
			}
			return json(200, page(list));
		}
		return route.fallback();
	});
}
