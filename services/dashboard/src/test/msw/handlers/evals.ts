import { HttpResponse, http } from "msw";
import { BASE } from "../data";

/**
 * Golden sets, test runs, samples and comparisons of a knowledge base (C7), created per test.
 * Indian demo data; shapes mirror `api/schemas/evals.py`.
 */
const NOW = "2026-10-02T09:00:00Z";
export const SET_ID = "5e700000-0000-4000-8000-000000000001";
export const RUN_A = "7a000000-0000-4000-8000-00000000000a";
export const RUN_B = "7a000000-0000-4000-8000-00000000000b";
export const RUN_PUBLISH = "7a000000-0000-4000-8000-00000000000c";
export const PAIR_1 = "9a000000-0000-4000-8000-000000000001";
export const PAIR_2 = "9a000000-0000-4000-8000-000000000002";

const pair = (id: string, position: number, query: string, over: Record<string, unknown>) => ({
	id,
	set_id: SET_ID,
	query,
	expected_answer: null,
	expected_behaviour: null,
	expected_label: null,
	must_include: [],
	must_cite: null,
	history: [],
	language: "en",
	page_url: null,
	office_hours: null,
	origin: "human",
	difficulty: null,
	topic: null,
	position,
	...over,
});

export const PAIRS = [
	pair(PAIR_1, 0, "Do I need to fast for a lipid profile?", {
		expected_answer: "Yes, 10–12 hours; water is fine.",
		expected_behaviour: "answer",
		must_include: ["10-12 hours"],
		must_cite: "test-prep-guide.pdf",
		difficulty: "easy",
		topic: "test prep",
	}),
	pair(PAIR_2, 1, "Kya aap MRI karte ho?", {
		expected_behaviour: "no_answer",
		language: "hi-Latn",
		origin: "from_feedback",
		difficulty: "hard",
		topic: "services",
	}),
];

const metric = (name: string, mean: number) => ({
	metric: name,
	n: 2,
	mean,
	ci_low: Math.max(0, mean - 0.2),
	ci_high: Math.min(1, mean + 0.2),
	threshold: 0.7,
	pass_rate: 0.5,
	small_sample: true,
});

export const run = (id: string, name: string | null, correctness: number, over: Record<string, unknown> = {}) => ({
	id,
	kind: "golden",
	name,
	set_id: SET_ID,
	mode: "full",
	target: "draft",
	config_version: null,
	status: "completed",
	triggered_by: "manual",
	sample_size: 2,
	totals: { questions: 2, passed: 1, failed: 1, errors: 0 },
	judge_model: "judge-x",
	estimated_cost_usd: 0.11,
	actual_cost_usd: 0.09,
	actual_cost_known: true,
	created_by: "owner Priya Kulkarni",
	started_at: NOW,
	completed_at: NOW,
	error_message: null,
	settings: { assistant: "draft" },
	metrics: [metric("answer_correctness", correctness)],
	tags: [{ tag: "difficulty", value: "hard", n: 1, means: { answer_correctness: 0.3 }, pass_rate: 0 }],
	warnings: ["Only 2 questions: confidence intervals are wide."],
	...over,
});

export const SAMPLES = [
	{
		id: "5a000000-0000-4000-8000-000000000001",
		golden_pair_id: PAIR_1,
		question: PAIRS[0]?.query,
		expected_answer: PAIRS[0]?.expected_answer,
		answer: "Yes. Fast for **10–12 hours** [1].",
		contexts: [{ text: "Fast 10–12 hours before the test." }],
		scores: { answer_correctness: 0.9, "criterion:mentions-home-fee": 1 },
		reasons: {},
		behaviour: null,
		tags: { origin: "human", difficulty: "easy", topic: "test prep" },
		passed: true,
		latency_ms: 900,
		cost_usd: 0.004,
		rating: null,
		rated_by: null,
		error: null,
	},
	{
		id: "5a000000-0000-4000-8000-000000000002",
		golden_pair_id: PAIR_2,
		question: PAIRS[1]?.query,
		expected_answer: null,
		answer: "Haan, hum MRI karte hain.",
		contexts: [],
		scores: { answer_correctness: null },
		reasons: { summary: "Claims MRI is offered; the documents list only pathology tests." },
		behaviour: {
			expected: "no_answer",
			label: "business_question",
			route: "grounded",
			checks: [{ name: "behaviour", status: "fail", detail: "expected no_answer, got answer (route grounded)" }],
		},
		tags: { origin: "from_feedback", difficulty: "hard", topic: "services" },
		passed: false,
		latency_ms: 800,
		cost_usd: 0.004,
		rating: null,
		rated_by: null,
		error: null,
	},
];

const SET_OUT = {
	id: SET_ID,
	corpus_id: "",
	name: "Patient help basics",
	source: "owner",
	is_default: true,
	pair_count: 2,
	behaviour_count: 2,
	last_run: null,
	created_at: NOW,
	updated_at: NOW,
};

export interface Logged {
	method: string;
	path: string;
	body?: unknown;
}

export function createEvalsMock(opts: { publishRun?: boolean } = {}) {
	const log: Logged[] = [];
	const settings = {
		judge_provider: null as string | null,
		judge_model: null as string | null,
		judge_has_key: false,
		judge_base_url: null as string | null,
		thresholds: {} as Record<string, number>,
		effective_thresholds: {
			faithfulness: 0.9,
			answer_relevancy: 0.85,
			context_precision: 0.8,
			context_recall: 0.75,
			answer_correctness: 0.7,
			context_relevance: 0.7,
		} as Record<string, number>,
	};
	const criteria = [
		{
			id: "c1000000-0000-4000-8000-000000000001",
			name: "mentions-home-fee",
			definition: "Must mention the home-collection fee (₹150 within Pune)",
			threshold: 0.7,
			enabled: true,
		},
	];
	const runs = () => [
		...(opts.publishRun
			? [
					run(RUN_PUBLISH, null, 0.9, {
						triggered_by: "publish",
						config_version: 7,
						mode: "fast",
						totals: { questions: 12, passed: 11, failed: 1, errors: 0 },
					}),
				]
			: []),
		run(RUN_B, "rerank on", 0.8),
		run(RUN_A, "baseline", 0.5),
	];
	return {
		log,
		handlers: [
			http.get(`${BASE}/api/v1/corpora/:kb/golden-sets`, () => HttpResponse.json({ items: [SET_OUT], count: 1 })),
			http.post(`${BASE}/api/v1/corpora/:kb/golden-sets`, async ({ request }) => {
				const body = await request.json();
				log.push({ method: "POST", path: "golden-sets", body });
				return HttpResponse.json(
					{ ...SET_OUT, id: "5e700000-0000-4000-8000-000000000002", is_default: false },
					{ status: 201 },
				);
			}),
			http.post(`${BASE}/api/v1/corpora/:kb/golden-sets/from-template`, async ({ request }) => {
				log.push({ method: "POST", path: "from-template", body: await request.json() });
				return HttpResponse.json({ ...SET_OUT, source: "template" }, { status: 201 });
			}),
			http.delete(`${BASE}/api/v1/corpora/:kb/golden-sets/:id`, ({ params }) => {
				log.push({ method: "DELETE", path: `golden-sets/${String(params.id)}` });
				return new HttpResponse(null, { status: 204 });
			}),
			http.post(`${BASE}/api/v1/corpora/:kb/golden-sets/:id/pairs`, async ({ request }) => {
				log.push({ method: "POST", path: "pairs", body: await request.json() });
				return HttpResponse.json({ ...SET_OUT, pairs: PAIRS }, { status: 201 });
			}),
			http.get(`${BASE}/api/v1/corpora/:kb/golden-sets/:id`, () => HttpResponse.json({ ...SET_OUT, pairs: PAIRS })),
			http.get(`${BASE}/api/v1/corpora/:kb/eval/criteria`, () => HttpResponse.json({ items: criteria })),
			http.post(`${BASE}/api/v1/corpora/:kb/eval/criteria`, async ({ request }) => {
				const body = (await request.json()) as Record<string, unknown>;
				log.push({ method: "POST", path: "criteria", body });
				const created = {
					id: `c1000000-0000-4000-8000-00000000000${criteria.length + 1}`,
					...body,
				} as (typeof criteria)[number];
				criteria.push(created);
				return HttpResponse.json(created, { status: 201 });
			}),
			http.get(`${BASE}/api/v1/corpora/:kb/eval/settings`, () => HttpResponse.json(settings)),
			http.put(`${BASE}/api/v1/corpora/:kb/eval/settings`, async ({ request }) => {
				const body = (await request.json()) as Record<string, unknown>;
				log.push({ method: "PUT", path: "settings", body });
				settings.judge_provider = (body.judge_provider as string | null) ?? null;
				settings.judge_model = (body.judge_model as string | null) ?? null;
				settings.judge_has_key = settings.judge_has_key || Boolean(body.judge_api_key);
				return HttpResponse.json(settings);
			}),
			http.post(`${BASE}/api/v1/corpora/:kb/eval/estimate`, async ({ request }) => {
				log.push({ method: "POST", path: "estimate", body: await request.json() });
				return HttpResponse.json({
					questions: 2,
					answer_usd: 0.03,
					judge_usd: 0.08,
					total_usd: 0.11,
					unpriced_models: [],
					assumptions: {},
					answer_model: "answer-model",
					judge_model: "judge-x",
				});
			}),
			http.post(`${BASE}/api/v1/corpora/:kb/eval/runs`, async ({ request }) => {
				const body = (await request.json()) as { mode: string; confirm_cost_usd?: number };
				log.push({ method: "POST", path: "runs", body });
				if (body.mode === "full" && (body.confirm_cost_usd ?? 0) < 0.11) {
					return HttpResponse.json(
						{ detail: "Confirm the estimated cost", code: "eval_cost_unconfirmed", estimated_cost_usd: 0.11 },
						{ status: 409 },
					);
				}
				return HttpResponse.json({ ...run(RUN_B, "rerank on", 0.8), status: "queued" }, { status: 202 });
			}),
			http.get(`${BASE}/api/v1/corpora/:kb/eval/runs/:id/samples`, ({ request }) => {
				const failedOnly = new URL(request.url).searchParams.get("failed_only") === "true";
				const items = failedOnly ? SAMPLES.filter((s) => !s.passed) : SAMPLES;
				return HttpResponse.json({ items, count: items.length, total: items.length });
			}),
			http.get(`${BASE}/api/v1/corpora/:kb/eval/runs/:id`, ({ params }) =>
				HttpResponse.json(runs().find((r) => r.id === params.id) ?? run(RUN_B, "rerank on", 0.8)),
			),
			http.get(`${BASE}/api/v1/corpora/:kb/eval/runs`, () => {
				const items = runs();
				return HttpResponse.json({ items, count: items.length });
			}),
			http.patch(`${BASE}/api/v1/corpora/:kb/eval/samples/:id`, async ({ request }) => {
				log.push({ method: "PATCH", path: "sample", body: await request.json() });
				return HttpResponse.json({ ok: true });
			}),
			http.post(`${BASE}/api/v1/corpora/:kb/eval/compare`, async ({ request }) => {
				log.push({ method: "POST", path: "compare", body: await request.json() });
				const a = run(RUN_A, "baseline", 0.5);
				const b = run(RUN_B, "rerank on", 0.8);
				return HttpResponse.json({
					runs: [a, b],
					metrics: [
						{
							metric: "answer_correctness",
							per_run: [a.metrics[0], b.metrics[0]],
							deltas: [null, { n: 2, mean: 0.3, ci_low: -0.1, ci_high: 0.7, significant: false }],
						},
					],
					questions: [
						{
							golden_pair_id: PAIR_1,
							question: PAIRS[0]?.query,
							per_run_passed: [false, true],
							per_run_mean: [0.5, 0.9],
							change: ["baseline", "improved"],
						},
					],
					warnings: ["Some runs have fewer than 30 questions: differences may be noise."],
				});
			}),
		],
	};
}

export const evalsHandlers = createEvalsMock().handlers;
