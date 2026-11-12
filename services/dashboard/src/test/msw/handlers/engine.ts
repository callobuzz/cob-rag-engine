import { HttpResponse, http } from "msw";
import type { EngineInfo } from "@/lib/api/accounts-types";
import { BASE } from "../data";
import { HEALTH_OK } from "./health";
import { INFO } from "./identity";

/**
 * Engine info (`GET /info` in the real InfoResponse shape), health and the engine-wide retrieval
 * defaults (`GET/PUT /admin/config/retrieval`, bounds like `UpdateRetrievalConfigRequest`).
 */
export const FULL_INFO: EngineInfo = {
	...INFO,
	features: {
		content_portal: true,
		landing: false, // LP1 landing
		streaming: true,
		transcription: { enabled: true, provider: "whisper" },
		reranker: { local: true, llm: true, default_provider: "local", enabled_by_default: false },
		web_search: { enabled: false, provider: "none" },
		accounts: true,
		cob: { enabled: true, public: true, corpus_id: null, docs_version: null },
		billing: { enabled: true }, // BIL2
		eval_ragas: true,
		website_learning: { enabled: true, render: false }, // BIZ1
		local_llm: { enabled: true, ready: true }, // PIP2
	},
	llm: {
		provider: "openrouter",
		model: "openai/gpt-oss-20b",
		fast_model: "openai/gpt-oss-20b",
		thinking: "low",
		fast_thinking: "off",
		language_models: {},
		key_configured: true,
	},
	public_url: "http://127.0.0.1:8000",
	public_url_configured: false,
};

/** `GET /admin/cob/status` (CobStatusOut): COB's docs are synced and current. */
export const COB_STATUS_OK = {
	docs_version: "0.1.0+6186e350818a",
	current_version: "0.1.0+6186e350818a",
	up_to_date: true,
	sync_state: "idle" as "idle" | "queued" | "running" | "failed",
	started_at: "2026-10-04T08:58:00Z",
	synced_at: "2026-10-04T09:00:00Z",
	document_count: 1160,
	last_error: null as string | null,
	eval: null as Record<string, unknown> | null,
	corpus_id: "0b0c7c9e-1d23-4c3e-9b3a-6a5c2f7d1e11",
};

// COB7: `GET /admin/cob/review` (CobReviewItem)
export interface ReviewItem {
	group: string;
	question: string;
	count: number;
	last_at: string;
	role: string;
	language: string;
	top_slug: string | null;
	reasons: ("no_answer" | "claims_removed" | "thumbs_down")[];
	comments: string[];
}

const BOUNDS: Record<string, [number, number | null, boolean]> = {
	top_k_dense: [1, 200, true],
	top_k_sparse: [1, 200, true],
	rrf_k: [1, 1000, true],
	mmr_lambda: [0, 1, false],
	temporal_decay_half_life_days: [0, null, false],
	rerank_top_n_input: [1, 500, true],
	rerank_top_k_output: [1, 200, true],
};

export function createEngineMock(opts: { down?: string[] } = {}) {
	const state = {
		retrieval: {
			top_k_dense: 20,
			top_k_sparse: 20,
			rrf_k: 60,
			mmr_lambda: 0.7,
			temporal_decay_half_life_days: 30,
			rerank_top_n_input: 50,
			rerank_top_k_output: 10,
			source: "defaults",
		} as Record<string, number | string>,
		puts: [] as Record<string, unknown>[],
		healthCalls: 0,
		cob: { ...COB_STATUS_OK },
		resyncs: [] as Record<string, unknown>[],
		// COB7
		review: [] as ReviewItem[],
		reviewQueries: [] as Record<string, string>[],
	};
	const handlers = [
		http.get(`${BASE}/api/v1/info`, () => HttpResponse.json(FULL_INFO)),
		http.get(`${BASE}/api/v1/health`, () => {
			state.healthCalls++;
			const services = { ...HEALTH_OK.services } as Record<string, boolean>;
			for (const s of opts.down ?? []) services[s] = false;
			return HttpResponse.json({ status: opts.down?.length ? "degraded" : "ok", services });
		}),
		http.get(`${BASE}/api/v1/admin/cob/status`, () => HttpResponse.json(state.cob)),
		// COB7
		http.get(`${BASE}/api/v1/admin/cob/review`, ({ request }) => {
			const url = new URL(request.url);
			state.reviewQueries.push(Object.fromEntries(url.searchParams));
			const down = url.searchParams.get("rating") === "down";
			return HttpResponse.json({
				items: state.review.filter((i) => !down || i.reasons.includes("thumbs_down")),
			});
		}),
		// end COB7
		http.post(`${BASE}/api/v1/admin/cob/resync`, async ({ request }) => {
			state.resyncs.push((await request.json()) as Record<string, unknown>);
			if (state.cob.sync_state === "queued" || state.cob.sync_state === "running") {
				return HttpResponse.json({ queued: false }, { status: 202 });
			}
			state.cob = { ...state.cob, sync_state: "queued" };
			return HttpResponse.json({ queued: true }, { status: 202 });
		}),
		http.get(`${BASE}/api/v1/admin/config/retrieval`, () => HttpResponse.json(state.retrieval)),
		http.put(`${BASE}/api/v1/admin/config/retrieval`, async ({ request }) => {
			const body = (await request.json()) as Record<string, number>;
			state.puts.push(body);
			const errors = [];
			for (const [k, v] of Object.entries(body)) {
				const b = BOUNDS[k];
				if (!b) continue;
				const [min, max] = b;
				const bad = k === "temporal_decay_half_life_days" ? v <= min : v < min || (max != null && v > max);
				if (bad) errors.push({ loc: ["body", k], msg: `Input should be between ${min} and ${max}` });
			}
			if (errors.length) return HttpResponse.json({ detail: errors }, { status: 422 });
			state.retrieval = { ...state.retrieval, ...body, source: "overrides" };
			return HttpResponse.json(state.retrieval);
		}),
	];
	return { state, handlers };
}

/** OpenRouter discovery: a good key lists two models; anything else is a 502 like the engine. */
export const OPENROUTER_KEY = "sk-or-test-key-not-real";
export function openRouterHandlers(seen: (string | null)[]) {
	return [
		http.post(`${BASE}/api/v1/models/discover/openrouter`, ({ request }) => {
			const key = request.headers.get("x-openrouter-key");
			seen.push(key);
			if (!key)
				return HttpResponse.json(
					{ detail: [{ loc: ["header", "x-openrouter-key"], msg: "Field required", type: "missing" }] },
					{ status: 422 },
				);
			if (key !== OPENROUTER_KEY)
				return HttpResponse.json({ detail: "Failed to discover models from OpenRouter" }, { status: 502 });
			return HttpResponse.json({
				categories: ["budget", "standard"],
				count: 2,
				models: [
					{
						id: "meta-llama/llama-3.3-70b-instruct",
						name: "Llama 3.3 70B",
						provider: "openrouter",
						category: "budget",
						context_window: 131072,
						input_cost_per_1m: 0.12,
						output_cost_per_1m: 0.3,
						recommended: false,
						supports_streaming: true,
					},
					{
						id: "sarvamai/sarvam-m",
						name: "Sarvam-M",
						provider: "openrouter",
						category: "standard",
						context_window: 32768,
						input_cost_per_1m: 0.25,
						output_cost_per_1m: 0.75,
						recommended: false,
						supports_streaming: true,
					},
				],
			});
		}),
	];
}
