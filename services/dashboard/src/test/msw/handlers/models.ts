import { HttpResponse, http } from "msw";
import { BASE } from "../data";
import { PRESETS } from "./corpora";

export const EMBEDDINGS = [
	{
		id: "bge-m3",
		provider: "tei",
		model: "BAAI/bge-m3",
		name: "BAAI/bge-m3",
		dimensions: 1024,
		notes: "multilingual",
		multilingual: true,
		multilingual_source: "https://huggingface.co/BAAI/bge-m3",
	},
	{
		id: "bge-base",
		provider: "tei",
		model: "BAAI/bge-base-en-v1.5",
		name: "BAAI/bge-base-en-v1.5",
		dimensions: 768,
		notes: "English",
		multilingual: false,
		multilingual_source: "https://huggingface.co/BAAI/bge-base-en-v1.5",
	},
	{
		id: "jina-v3",
		provider: "jina",
		model: "jina-embeddings-v3",
		name: "jina-embeddings-v3",
		dimensions: 1024,
		notes: "hosted",
		multilingual: true,
		multilingual_source: "https://docs.jina.ai",
	},
];

export const MODELS = [
	{
		id: "openai/gpt-oss-20b",
		name: "GPT-OSS 20B",
		provider: "openrouter",
		category: "budget",
		context_window: 131072,
		input_cost_per_1m: 0.05,
		output_cost_per_1m: 0.2,
		recommended: true,
		supports_streaming: true,
	},
	{
		id: "anthropic/claude-haiku-4-5",
		name: "Claude Haiku 4.5",
		provider: "anthropic",
		category: "standard",
		context_window: 200000,
		input_cost_per_1m: 1,
		output_cost_per_1m: 5,
		recommended: false,
		supports_streaming: true,
	},
];

export const modelsHandlers = [
	http.get(`${BASE}/api/v1/models/presets`, () => HttpResponse.json({ count: 3, presets: PRESETS })),
	http.get(`${BASE}/api/v1/models/embeddings`, () => HttpResponse.json(EMBEDDINGS)),
	http.get(`${BASE}/api/v1/models`, () =>
		HttpResponse.json({ categories: ["budget", "standard"], count: MODELS.length, models: MODELS }),
	),
];

import ANSWER_MODELS from "./answer-models.fixture.json";
import BENCHMARK_SUMMARY from "./benchmark-summary.fixture.json";
import FAST_MODELS from "./fast-models.fixture.json";

/** The engine's real `benchmarks/results` files, served the way `GET /models/fast-benchmarks`, `/answer-benchmarks` and `/benchmark-summary` do. */
export const FAST_BENCHMARKS = { available: true, benchmarks: FAST_MODELS };
export const ANSWER_BENCHMARKS = { available: true, benchmarks: ANSWER_MODELS };
export const BENCHMARK_SUMMARY_RESPONSE = { available: true, summary: BENCHMARK_SUMMARY };

modelsHandlers.push(
	http.get(`${BASE}/api/v1/models/fast-benchmarks`, () => HttpResponse.json(FAST_BENCHMARKS)),
	http.get(`${BASE}/api/v1/models/answer-benchmarks`, () => HttpResponse.json(ANSWER_BENCHMARKS)),
	http.get(`${BASE}/api/v1/models/benchmark-summary`, () => HttpResponse.json(BENCHMARK_SUMMARY_RESPONSE)),
);
