import { HttpResponse, http } from "msw";
import { BASE, CORPORA } from "../data";

/**
 * Insights (D9) handlers, created per test (`server.use(...createInsightsMock().handlers)`), so
 * their state never leaks between tests and the shared server/setup files stay untouched:
 * history, `/query/{id}`, trends, count, confidence, gap reports (queued → completed on the
 * second read), feedback list/stats/create, memories (list, search, add, bulk, delete, bulk
 * delete, purge; A5: bulk delete and purge owner-only). Indian demo data only.
 */
const PATIENT = CORPORA[0]?.id ?? "";
const STAFF = CORPORA[1]?.id ?? "";

export interface Logged {
	method: string;
	path: string;
	query: Record<string, string>;
	body: unknown;
}

const Q = (n: number) => `0d0d0d0d-0000-4000-8000-${String(n).padStart(12, "0")}`;
export const QUESTION_IDS = { lipid: Q(1), hindi: Q(2), sunday: Q(3), sop: Q(4) };

function seedHistory() {
	return [
		{
			id: QUESTION_IDS.lipid,
			corpus_id: PATIENT,
			query_text: "Do I need to fast for a lipid profile?",
			response: "Yes. Fast for 10–12 hours before a lipid profile; water is fine.",
			confidence_score: 0.93,
			latency_ms: 840,
			session_id: "visitor-17",
			created_at: "2026-09-30T08:10:00Z",
		},
		{
			id: QUESTION_IDS.hindi,
			corpus_id: PATIENT,
			query_text: "लिपिड प्रोफाइल के लिए उपवास ज़रूरी है?",
			response: "हाँ, 10–12 घंटे उपवास करें।",
			confidence_score: 0.64,
			latency_ms: 1320,
			session_id: "visitor-18",
			created_at: "2026-09-30T07:40:00Z",
		},
		{
			id: QUESTION_IDS.sunday,
			corpus_id: PATIENT,
			query_text: "Home collection in Baner on Sunday?",
			response: "I couldn't find that in the documents.",
			confidence_score: 0.38,
			latency_ms: 2100,
			session_id: "visitor-17",
			created_at: "2026-09-29T18:05:00Z",
		},
		{
			id: QUESTION_IDS.sop,
			corpus_id: STAFF,
			query_text: "How do we label a fasting sample?",
			response: "Use the yellow FAST sticker.",
			confidence_score: null,
			latency_ms: 900,
			session_id: null,
			created_at: "2026-09-29T09:00:00Z",
		},
	];
}

interface Cluster {
	topic_id: number;
	query_count: number;
	keywords: string[];
	representative_queries: string[];
}
interface Gap {
	id: string;
	corpus_id: string | null;
	status: string;
	query_count: number;
	cluster_count: number;
	started_at: string;
	completed_at: string | null;
	clusters: Cluster[] | null;
}

export const GAP_DONE_ID = "9b9b9b9b-0000-4000-8000-000000000001";

export function createInsightsMock(opts: { memberSession?: boolean; noEmbedder?: boolean } = {}) {
	const log: Logged[] = [];
	const history = seedHistory();
	const feedback = [
		{
			id: "fb000000-0000-4000-8000-000000000001",
			query_id: QUESTION_IDS.sunday,
			corpus_id: PATIENT,
			question: "Home collection in Baner on Sunday?",
			rating: 1,
			correction: "Yes, Sundays 7–11 am in Baner; book by Saturday evening.",
			feedback_type: "explicit",
			session_id: null,
			created_at: "2026-09-29T18:10:00Z",
		},
		{
			id: "fb000000-0000-4000-8000-000000000002",
			query_id: QUESTION_IDS.lipid,
			corpus_id: PATIENT,
			question: "Do I need to fast for a lipid profile?",
			rating: 5,
			correction: null,
			feedback_type: "explicit",
			session_id: null,
			created_at: "2026-09-30T08:12:00Z",
		},
	];
	const gaps: Gap[] = [
		{
			id: GAP_DONE_ID,
			corpus_id: PATIENT,
			status: "completed",
			query_count: 12,
			cluster_count: 2,
			started_at: "2026-09-29T02:00:00Z",
			completed_at: "2026-09-29T02:03:00Z",
			clusters: [
				{
					topic_id: 0,
					query_count: 4,
					keywords: ["hba1c", "diabetes", "package"],
					representative_queries: ["Is HbA1c included in the diabetes package?"],
				},
				{
					topic_id: 1,
					query_count: 7,
					keywords: ["home collection", "baner", "sunday"],
					representative_queries: ["Home collection in Baner on Sunday?", "रविवारी घरून नमुना घेता का?"],
				},
			],
		},
	];
	const reads = new Map<string, number>();
	const memories: {
		id: string;
		corpus_id: string;
		user_id: string;
		memory_type: string;
		content: string;
		category: string | null;
		related_entity: string | null;
		confidence: number;
		source: string;
		metadata: null;
		created_at: string;
	}[] = [
		{
			id: "a1a1a1a1-0000-4000-8000-000000000001",
			corpus_id: PATIENT,
			user_id: "visitor-17",
			memory_type: "preference",
			content: "Prefers home collection in Kothrud before 9 am",
			category: "visits",
			related_entity: "home collection",
			confidence: 1,
			source: "explicit",
			metadata: null,
			created_at: "2026-09-28T09:00:00Z",
		},
		{
			id: "a1a1a1a1-0000-4000-8000-000000000002",
			corpus_id: PATIENT,
			user_id: "visitor-18",
			memory_type: "fact",
			content: "Diabetic; takes metformin",
			category: null,
			related_entity: null,
			confidence: 0.9,
			source: "implicit",
			metadata: null,
			created_at: "2026-09-27T09:00:00Z",
		},
	];

	const record = async (request: Request) => {
		const url = new URL(request.url);
		let body: unknown = null;
		if (request.method !== "GET" && request.method !== "DELETE") {
			const text = await request.clone().text();
			try {
				body = text ? JSON.parse(text) : null;
			} catch {
				body = text;
			}
		}
		log.push({ method: request.method, path: url.pathname, query: Object.fromEntries(url.searchParams), body });
		return url;
	};
	const page = <T>(items: T[], url: URL, def = 20) => {
		const limit = Number(url.searchParams.get("limit") ?? def);
		const offset = Number(url.searchParams.get("offset") ?? 0);
		return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
	};
	const ownerOnly = () =>
		opts.memberSession
			? HttpResponse.json({ detail: "Only a workspace owner can do this", code: "owner_required" }, { status: 403 })
			: null;

	const handlers = [
		http.get(`${BASE}/api/v1/analytics/queries/history`, async ({ request }) => {
			const url = await record(request);
			const corpus = url.searchParams.get("corpus_id");
			const session = url.searchParams.get("session_id");
			const rows = history.filter((h) => (!corpus || h.corpus_id === corpus) && (!session || h.session_id === session));
			return HttpResponse.json(page(rows, url));
		}),
		http.get(`${BASE}/api/v1/query/:id`, async ({ request, params }) => {
			await record(request);
			const h = history.find((x) => x.id === params.id);
			if (!h) return HttpResponse.json({ detail: "Query not found" }, { status: 404 });
			return HttpResponse.json({
				id: h.id,
				query_text: h.query_text,
				response: h.response,
				confidence_score: h.confidence_score,
				latency_ms: h.latency_ms,
				model_used: "openai/gpt-oss-20b",
				token_usage: { prompt_tokens: 1100, completion_tokens: 140, total_tokens: 1240 },
				created_at: h.created_at,
			});
		}),
		http.get(`${BASE}/api/v1/analytics/queries/trends`, async ({ request }) => {
			await record(request);
			return HttpResponse.json({
				data: [
					{ bucket: "2026-09-28T00:00:00+00:00", count: 40, avg_confidence: 0.82, avg_latency: 900 },
					{ bucket: "2026-09-29T00:00:00+00:00", count: 64, avg_confidence: 0.71, avg_latency: 1100 },
					{ bucket: "2026-09-30T00:00:00+00:00", count: 12, avg_confidence: null, avg_latency: 700 },
				],
			});
		}),
		http.get(`${BASE}/api/v1/analytics/queries/count`, async ({ request }) => {
			await record(request);
			return HttpResponse.json({ count: 116 });
		}),
		http.get(`${BASE}/api/v1/analytics/queries/confidence`, async ({ request }) => {
			await record(request);
			return HttpResponse.json({
				buckets: [
					{ range: "0.0-0.2", count: 2 },
					{ range: "0.2-0.4", count: 5 },
					{ range: "0.4-0.6", count: 9 },
					{ range: "0.6-0.8", count: 30 },
					{ range: "0.8-1.0", count: 58 },
				],
			});
		}),
		http.get(`${BASE}/api/v1/analytics/gaps`, async ({ request }) => {
			const url = await record(request);
			const corpus = url.searchParams.get("corpus_id");
			const rows = gaps.filter((g) => !corpus || g.corpus_id === corpus);
			const p = page(rows, url);
			return HttpResponse.json({ items: p.items, total: p.total });
		}),
		http.post(`${BASE}/api/v1/analytics/gaps/run`, async ({ request }) => {
			const url = await record(request);
			if (opts.noEmbedder)
				return HttpResponse.json({ detail: "No embedder available for gap detection" }, { status: 503 });
			const corpus = url.searchParams.get("corpus_id");
			if (!corpus)
				return HttpResponse.json({ detail: "corpus_id is required unless authenticated as admin." }, { status: 400 });
			const r: Gap = {
				id: `9b9b9b9b-0000-4000-8000-${String(gaps.length + 1).padStart(12, "0")}`,
				corpus_id: corpus,
				status: "queued",
				query_count: 0,
				cluster_count: 0,
				started_at: "2026-09-30T10:00:00Z",
				completed_at: null,
				clusters: null,
			};
			gaps.unshift(r);
			return HttpResponse.json(r, { status: 202 });
		}),
		http.get(`${BASE}/api/v1/analytics/gaps/:id`, async ({ request, params }) => {
			await record(request);
			const g = gaps.find((x) => x.id === params.id);
			if (!g) return HttpResponse.json({ detail: "Gap report not found" }, { status: 404 });
			const n = (reads.get(g.id) ?? 0) + 1;
			reads.set(g.id, n);
			if (g.status !== "completed" && n >= 2) {
				g.status = "completed";
				g.query_count = 5;
				g.cluster_count = 1;
				g.completed_at = "2026-09-30T10:02:00Z";
				g.clusters = [
					{
						topic_id: 0,
						query_count: 5,
						keywords: ["thyroid", "tsh", "fasting"],
						representative_queries: ["Is fasting needed for a thyroid test?"],
					},
				];
			}
			return HttpResponse.json(g);
		}),
		http.get(`${BASE}/api/v1/admin/gaps`, async ({ request }) => {
			await record(request);
			const g = gaps[0];
			if (!g) return HttpResponse.json({ detail: "No gap reports available" }, { status: 404 });
			return HttpResponse.json({
				id: g.id,
				corpus_id: null,
				status: g.status,
				created_at: g.started_at,
				topics: g.clusters ?? [],
				total_queries_analyzed: g.query_count,
				low_confidence_count: 0,
			});
		}),
		http.post(`${BASE}/api/v1/admin/gaps/trigger`, async ({ request }) => {
			await record(request);
			const r: Gap = {
				id: "9b9b9b9b-0000-4000-8000-0000000000aa",
				corpus_id: null,
				status: "queued",
				query_count: 0,
				cluster_count: 0,
				started_at: "2026-09-30T11:00:00Z",
				completed_at: null,
				clusters: null,
			};
			gaps.unshift(r);
			return HttpResponse.json({ gap_report_id: r.id, status: "queued" }, { status: 202 });
		}),
		http.get(`${BASE}/api/v1/feedback`, async ({ request }) => {
			const url = await record(request);
			const corpus = url.searchParams.get("corpus_id");
			const min = Number(url.searchParams.get("min_rating") ?? 1);
			const max = Number(url.searchParams.get("max_rating") ?? 5);
			const rows = feedback.filter(
				(f) => (!corpus || f.corpus_id === corpus) && (f.rating ?? 0) >= min && (f.rating ?? 0) <= max,
			);
			return HttpResponse.json(page(rows, url));
		}),
		http.get(`${BASE}/api/v1/feedback/stats`, async ({ request }) => {
			const url = await record(request);
			return HttpResponse.json({
				corpus_id: url.searchParams.get("corpus_id"),
				total: feedback.length,
				positive: feedback.filter((f) => (f.rating ?? 0) >= 4).length,
				negative: feedback.filter((f) => (f.rating ?? 0) <= 2).length,
				corrections: feedback.filter((f) => f.correction).length,
				implicit: 0,
				start: "2026-08-31T10:00:00Z",
				end: "2026-09-30T10:00:00Z",
			});
		}),
		http.post(`${BASE}/api/v1/feedback`, async ({ request }) => {
			await record(request);
			const b = (await request.clone().json()) as { query_id: string; rating: number; correction?: string | null };
			const h = history.find((x) => x.id === b.query_id);
			if (!h) return HttpResponse.json({ detail: "Query not found" }, { status: 404 });
			const id = `fb000000-0000-4000-8000-${String(feedback.length + 1).padStart(12, "0")}`;
			feedback.unshift({
				id,
				query_id: h.id,
				corpus_id: h.corpus_id,
				question: h.query_text,
				rating: b.rating,
				correction: b.correction ?? null,
				feedback_type: "explicit",
				session_id: null,
				created_at: "2026-09-30T10:30:00Z",
			});
			return HttpResponse.json({ feedback_id: id, message: "Feedback recorded successfully" }, { status: 201 });
		}),
		http.get(`${BASE}/api/v1/corpora/:kb/memories`, async ({ request, params }) => {
			const url = await record(request);
			const type = url.searchParams.get("memory_type");
			const user = url.searchParams.get("user_id");
			const rows = memories.filter(
				(m) => m.corpus_id === params.kb && (!type || m.memory_type === type) && (!user || m.user_id === user),
			);
			return HttpResponse.json(page(rows, url, 50));
		}),
		http.post(`${BASE}/api/v1/corpora/:kb/memories/search`, async ({ request, params }) => {
			await record(request);
			const b = (await request.clone().json()) as { user_id: string; query: string };
			const items = memories.filter(
				(m) =>
					m.corpus_id === params.kb &&
					m.user_id === b.user_id &&
					m.content.toLowerCase().includes(b.query.toLowerCase()),
			);
			return HttpResponse.json({ items, query: b.query, total: items.length });
		}),
		http.post(`${BASE}/api/v1/corpora/:kb/memories/bulk`, async ({ request, params }) => {
			await record(request);
			const b = (await request.clone().json()) as { memories: Record<string, unknown>[] };
			const created = b.memories.map((m, i) => ({
				id: `a1a1a1a1-0000-4000-8000-${String(100 + i).padStart(12, "0")}`,
				corpus_id: String(params.kb),
				user_id: String(m.user_id),
				memory_type: String(m.memory_type ?? "fact"),
				content: String(m.content),
				category: null,
				related_entity: null,
				confidence: 1,
				source: "explicit",
				metadata: null,
				created_at: "2026-09-30T10:00:00Z",
			}));
			memories.unshift(...created);
			return HttpResponse.json(created, { status: 201 });
		}),
		http.post(`${BASE}/api/v1/corpora/:kb/memories`, async ({ request, params }) => {
			await record(request);
			const b = (await request.clone().json()) as Record<string, unknown>;
			const m = {
				id: `a1a1a1a1-0000-4000-8000-${String(200 + memories.length).padStart(12, "0")}`,
				corpus_id: String(params.kb),
				user_id: String(b.user_id),
				memory_type: String(b.memory_type ?? "fact"),
				content: String(b.content),
				category: (b.category as string | null) ?? null,
				related_entity: (b.related_entity as string | null) ?? null,
				confidence: Number(b.confidence ?? 1),
				source: String(b.source ?? "explicit"),
				metadata: null,
				created_at: "2026-09-30T10:00:00Z",
			};
			memories.unshift(m);
			return HttpResponse.json(m, { status: 201 });
		}),
		http.delete(`${BASE}/api/v1/corpora/:kb/memories/purge`, async ({ request }) => {
			await record(request);
			return ownerOnly() ?? HttpResponse.json({ deleted: 3 });
		}),
		http.get(`${BASE}/api/v1/corpora/:kb/memories/:id`, async ({ request, params }) => {
			await record(request);
			const m = memories.find((x) => x.id === params.id);
			return m ? HttpResponse.json(m) : HttpResponse.json({ detail: "Memory not found" }, { status: 404 });
		}),
		http.delete(`${BASE}/api/v1/corpora/:kb/memories/:id`, async ({ request, params }) => {
			await record(request);
			const i = memories.findIndex((x) => x.id === params.id);
			if (i < 0) return HttpResponse.json({ detail: "Memory not found" }, { status: 404 });
			memories.splice(i, 1);
			return new HttpResponse(null, { status: 204 });
		}),
		http.delete(`${BASE}/api/v1/corpora/:kb/memories`, async ({ request, params }) => {
			const url = await record(request);
			const denied = ownerOnly();
			if (denied) return denied;
			const type = url.searchParams.get("memory_type");
			const user = url.searchParams.get("user_id");
			const before = memories.length;
			for (let i = memories.length - 1; i >= 0; i--) {
				const m = memories[i];
				if (m && m.corpus_id === params.kb && (!type || m.memory_type === type) && (!user || m.user_id === user))
					memories.splice(i, 1);
			}
			return HttpResponse.json({ deleted: before - memories.length });
		}),
	];

	return { handlers, log, history, feedback, gaps, memories };
}
