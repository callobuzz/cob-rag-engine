import { HttpResponse, http } from "msw";
import { BASE, CORPORA } from "../data";
import type { Logged } from "./insights";

/**
 * Quality (D9) handlers, created per test: golden pairs (CRUD, import with the engine's CSV
 * rules, export, from-feedback; A8: delete owner-only), evaluation and optimisation runs (queued
 * → completed on the second detail read). Indian demo data only.
 */
const PATIENT = CORPORA[0]?.id ?? "";

interface Pair {
	id: string;
	corpus_id: string;
	query: string;
	expected_answer: string | null;
	expected_behaviour?: string | null;
	must_include?: string[];
	difficulty?: string | null;
	topic?: string | null;
	origin?: string;
	set_id?: string | null;
	expected_chunk_ids: string[] | null;
	metadata: Record<string, unknown> | null;
	source: string;
	created_at: string;
}
interface Run {
	id: string;
	status: string;
	corpus_id: string | null;
	sample_size?: number | null;
	n_trials?: number | null;
	created_at: string;
	completed_at: string | null;
	error_message: string | null;
	metrics?: Record<string, unknown>;
	config_snapshot?: Record<string, unknown>;
	pareto_configs?: Record<string, unknown>[];
	best_config?: Record<string, unknown>;
}

const P = (n: number) => `901d0000-0000-4000-8000-${String(n).padStart(12, "0")}`;

export const EVAL_DONE = "e0a10000-0000-4000-8000-000000000001";
export const OPT_DONE = "0b710000-0000-4000-8000-000000000001";
export const OPT_ALL = "0b710000-0000-4000-8000-000000000002";

const BEST = {
	params: {
		retrieval_top_k_dense: 42,
		retrieval_top_k_sparse: 30,
		rrf_k: 55,
		mmr_lambda: 0.634,
		temporal_decay_half_life_days: 21.5,
		rerank_top_n_input: 40,
		rerank_top_k_output: 6,
	},
	metrics: { faithfulness: 0.91, answer_relevancy: 0.88, latency_ms: 1200 },
	trial_number: 7,
};

export function createQualityMock(opts: { memberSession?: boolean } = {}) {
	const log: Logged[] = [];
	const pairs: Pair[] = [
		{
			id: P(1),
			corpus_id: PATIENT,
			query: "Do I need to fast for a lipid profile?",
			expected_answer: "Yes, 10–12 hours; water is fine.",
			expected_chunk_ids: null,
			metadata: null,
			source: "manual",
			set_id: "5e700000-0000-4000-8000-000000000001",
			expected_behaviour: "answer",
			must_include: [],
			origin: "human",
			difficulty: "easy",
			topic: "test prep",
			created_at: "2026-09-28T10:00:00Z",
		},
	];
	const evals: Run[] = [
		{
			id: EVAL_DONE,
			status: "completed",
			corpus_id: PATIENT,
			sample_size: 20,
			created_at: "2026-09-29T10:00:00Z",
			completed_at: "2026-09-29T10:06:00Z",
			error_message: null,
			metrics: {
				faithfulness: { name: "faithfulness", score: 0.93, threshold: 0.9, passed: true },
				context_recall: { name: "context_recall", score: 0.61, threshold: 0.75, passed: false },
			},
			config_snapshot: { top_k: 20 },
		},
	];
	const optimize: Run[] = [
		{
			id: OPT_DONE,
			status: "completed",
			corpus_id: PATIENT,
			n_trials: 30,
			created_at: "2026-09-29T11:00:00Z",
			completed_at: "2026-09-29T11:40:00Z",
			error_message: null,
			pareto_configs: [BEST, { ...BEST, trial_number: 12, metrics: { faithfulness: 0.86, latency_ms: 800 } }],
			best_config: BEST,
		},
		{
			id: OPT_ALL,
			status: "completed",
			corpus_id: null,
			n_trials: 10,
			created_at: "2026-09-28T11:00:00Z",
			completed_at: "2026-09-28T11:20:00Z",
			error_message: null,
			pareto_configs: [BEST],
			best_config: BEST,
		},
	];
	const reads = new Map<string, number>();
	const uploads: string[] = [];

	const record = async (request: Request) => {
		const url = new URL(request.url);
		let body: unknown = null;
		const type = request.headers.get("content-type") ?? "";
		if (request.method !== "GET" && request.method !== "DELETE" && type.includes("json")) {
			body = await request.clone().json();
		}
		log.push({ method: request.method, path: url.pathname, query: Object.fromEntries(url.searchParams), body });
		return url;
	};
	const page = <T>(items: T[], url: URL, def: number) => {
		const limit = Number(url.searchParams.get("limit") ?? def);
		const offset = Number(url.searchParams.get("offset") ?? 0);
		return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
	};
	const addPair = (p: Omit<Pair, "id" | "created_at">) => {
		const pair: Pair = { ...p, id: P(pairs.length + 1 + uploads.length * 10), created_at: "2026-09-30T10:00:00Z" };
		pairs.unshift(pair);
		return pair;
	};
	const advance = (r: Run) => {
		const n = (reads.get(r.id) ?? 0) + 1;
		reads.set(r.id, n);
		if (r.status !== "completed" && n >= 2) {
			r.status = "completed";
			r.completed_at = "2026-09-30T10:05:00Z";
		}
	};

	const handlers = [
		http.get(`${BASE}/api/v1/golden/export`, async ({ request }) => {
			const url = await record(request);
			const items = pairs.filter((p) => p.corpus_id === url.searchParams.get("corpus_id"));
			return HttpResponse.json({ items, count: items.length });
		}),
		http.post(`${BASE}/api/v1/golden/import`, async ({ request }) => {
			const url = await record(request);
			// Unit tests send the CSV text with its name in x-file-name (see quality.test.tsx).
			const name = request.headers.get("x-file-name") ?? "";
			if (!name.endsWith(".csv")) return HttpResponse.json({ detail: "File must be a .csv" }, { status: 400 });
			const text = await request.text();
			uploads.push(text);
			const [header, ...rows] = text.replace(/\r/g, "").split("\n").filter(Boolean);
			const cols = (header ?? "").split(",");
			let n = 0;
			for (const row of rows) {
				const cells = row.split(",");
				const q = cells[cols.indexOf("query")]?.trim();
				const a = cells[cols.indexOf("expected_answer")]?.trim();
				if (!q || !a) continue;
				addPair({
					corpus_id: String(url.searchParams.get("corpus_id")),
					query: q,
					expected_answer: a,
					expected_chunk_ids: null,
					metadata: null,
					source: "import",
				});
				n++;
			}
			return HttpResponse.json({ imported_count: n, message: "Import complete" }, { status: 201 });
		}),
		http.post(`${BASE}/api/v1/golden/from-feedback`, async ({ request }) => {
			await record(request);
			const b = (await request.clone().json()) as { feedback_id: string };
			if (b.feedback_id.endsWith("2"))
				return HttpResponse.json({ detail: "Feedback fb has no correction text" }, { status: 400 });
			const pair = addPair({
				corpus_id: PATIENT,
				query: "Home collection in Baner on Sunday?",
				expected_answer: "Yes, Sundays 7–11 am in Baner; book by Saturday evening.",
				expected_chunk_ids: null,
				metadata: { feedback_id: b.feedback_id, original_rating: 1 },
				source: "feedback",
			});
			return HttpResponse.json(pair, { status: 201 });
		}),
		http.get(`${BASE}/api/v1/golden`, async ({ request }) => {
			const url = await record(request);
			const rows = pairs.filter((p) => p.corpus_id === url.searchParams.get("corpus_id"));
			const pg = page(rows, url, 50);
			return HttpResponse.json({ ...pg, count: pg.items.length });
		}),
		http.post(`${BASE}/api/v1/golden`, async ({ request }) => {
			await record(request);
			const b = (await request.clone().json()) as Pair;
			return HttpResponse.json(addPair({ ...b, metadata: null, source: "manual" }), { status: 201 });
		}),
		http.patch(`${BASE}/api/v1/golden/:id`, async ({ request, params }) => {
			await record(request);
			const p = pairs.find((x) => x.id === params.id);
			if (!p) return HttpResponse.json({ detail: "Golden pair not found" }, { status: 404 });
			Object.assign(p, await request.clone().json());
			return HttpResponse.json(p);
		}),
		http.delete(`${BASE}/api/v1/golden/:id`, async ({ request, params }) => {
			await record(request);
			if (opts.memberSession)
				return HttpResponse.json(
					{ detail: "Only a workspace owner can do this", code: "owner_required" },
					{ status: 403 },
				);
			const i = pairs.findIndex((x) => x.id === params.id);
			if (i < 0) return HttpResponse.json({ detail: "Golden pair not found" }, { status: 404 });
			pairs.splice(i, 1);
			return HttpResponse.json({ message: "Golden pair deleted successfully" });
		}),
		http.get(`${BASE}/api/v1/admin/eval/runs`, async ({ request }) => {
			const url = await record(request);
			return HttpResponse.json(page(evals, url, 20));
		}),
		http.get(`${BASE}/api/v1/admin/eval/runs/:id`, async ({ request, params }) => {
			await record(request);
			const r = evals.find((x) => x.id === params.id);
			if (!r) return HttpResponse.json({ detail: "Eval run not found" }, { status: 404 });
			advance(r);
			return HttpResponse.json({ metrics: {}, config_snapshot: {}, ...r });
		}),
		http.post(`${BASE}/api/v1/admin/eval/trigger`, async ({ request }) => {
			await record(request);
			const b = (await request.clone().json()) as { corpus_id: string | null; sample_size: number | null };
			const r: Run = {
				id: `e0a10000-0000-4000-8000-${String(evals.length + 1).padStart(12, "0")}`,
				status: "queued",
				corpus_id: b.corpus_id,
				sample_size: b.sample_size,
				created_at: "2026-09-30T10:00:00Z",
				completed_at: null,
				error_message: null,
				metrics: {},
				config_snapshot: {},
			};
			evals.unshift(r);
			return HttpResponse.json({ eval_run_id: r.id, status: "queued" }, { status: 202 });
		}),
		http.get(`${BASE}/api/v1/admin/optimize/runs`, async ({ request }) => {
			const url = await record(request);
			return HttpResponse.json(page(optimize, url, 20));
		}),
		http.get(`${BASE}/api/v1/admin/optimize/runs/:id`, async ({ request, params }) => {
			await record(request);
			const r = optimize.find((x) => x.id === params.id);
			if (!r) return HttpResponse.json({ detail: "Optimization run not found" }, { status: 404 });
			advance(r);
			return HttpResponse.json({ pareto_configs: [], best_config: {}, ...r });
		}),
		http.post(`${BASE}/api/v1/admin/optimize/trigger`, async ({ request }) => {
			await record(request);
			const b = (await request.clone().json()) as { corpus_id: string | null; n_trials: number | null };
			const r: Run = {
				id: `0b710000-0000-4000-8000-${String(optimize.length + 1).padStart(12, "0")}`,
				status: "queued",
				corpus_id: b.corpus_id,
				n_trials: b.n_trials,
				created_at: "2026-09-30T10:00:00Z",
				completed_at: null,
				error_message: null,
				pareto_configs: [],
				best_config: {},
			};
			optimize.unshift(r);
			return HttpResponse.json({ optimization_run_id: r.id, status: "queued" }, { status: 202 });
		}),
	];

	return { handlers, log, pairs, evals, optimize, uploads };
}
