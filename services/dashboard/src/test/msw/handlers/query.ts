import { HttpResponse, http } from "msw";
import { BASE } from "../data";

/**
 * Query handlers (D5) from `docs/openapi.json` and the engine's SSE format
 * (api/routes/query.py `stream_query`: named events `sources`, `token`, `done`, `error`).
 * SSE bodies are built with a ReadableStream so tests can split them anywhere. Indian demo data.
 */

export const QUERY_ID = "9e1f0000-0000-4000-8000-00000000a001";
export const CHUNK_FAST = "c0000000-0000-4000-8000-000000000001";
export const CHUNK_HOME = "c0000000-0000-4000-8000-000000000002";
export const CHUNK_SLOTS = "c0000000-0000-4000-8000-000000000003";

export const SOURCES = [
	{
		index: 1,
		chunk_id: CHUNK_FAST,
		document_id: "d0c00000-0000-4000-8000-000000000001",
		external_id: null,
		file_name: "test-prep-guide.pdf",
		section: "Lipid profile",
		page: 2,
		snippet: "Fast for 10–12 hours before the test. Plain water is allowed; avoid tea and coffee.",
		score: 0.91,
	},
	{
		index: 2,
		chunk_id: CHUNK_HOME,
		document_id: "d0c00000-0000-4000-8000-000000000002",
		external_id: null,
		file_name: "home-collection.pdf",
		section: "Charges",
		page: 1,
		snippet: "Home sample collection is free on orders above ₹999; otherwise ₹150 within Pune city.",
		score: 0.72,
	},
];

export const ANSWER_TOKENS = ["Yes. Fast for ", "10–12 hours ", "before a lipid profile; ", "plain water is fine [1]."];
export const ANSWER = ANSWER_TOKENS.join("");

export const RETRIEVE = {
	chunks: [
		{
			chunk_id: CHUNK_FAST,
			text: SOURCES[0]?.snippet ?? "",
			file_name: "test-prep-guide.pdf",
			document_id: SOURCES[0]?.document_id,
			external_id: null,
			section: "Lipid profile",
			page: 2,
			score: 0.91,
			token_count: 40,
			score_breakdown: {
				dense_score: 0.82,
				dense_rank: 1,
				sparse_score: 7.4,
				sparse_rank: 1,
				fused_score: 0.91,
				pipeline_rank: 1,
				rerank_score: null,
			},
		},
		{
			chunk_id: CHUNK_HOME,
			text: SOURCES[1]?.snippet ?? "",
			file_name: "home-collection.pdf",
			document_id: SOURCES[1]?.document_id,
			external_id: null,
			section: "Charges",
			page: 1,
			score: 0.72,
			token_count: 36,
			score_breakdown: null,
		},
	],
	processed_query: "fast before lipid profile test",
	latency_ms: 240,
	total_chunks: 2,
	stage_timings_ms: {
		query_understanding: 30,
		embed: 18,
		search_dense: 31,
		search_sparse: 12,
		fuse: 1,
		load_chunk_text: 6,
		mmr: 2,
		assemble: 4,
	},
};

export const QUERY_JSON = {
	query_id: QUERY_ID,
	answer: ANSWER,
	citations: SOURCES.map((s) => ({
		chunk_id: s.chunk_id,
		document_id: s.document_id,
		external_id: s.external_id,
		file_name: s.file_name,
		section: s.section,
		page: s.page,
		text_snippet: s.snippet,
		score: s.score,
	})),
	confidence: 0.94,
	latency_ms: 1840,
	model_used: "openai/gpt-oss-20b",
	token_usage: { prompt_tokens: 1000, completion_tokens: 200, total_tokens: 1200 },
};

export type SseEvent = [event: string, data: unknown];

export function sseText(events: SseEvent[]): string {
	return events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
}

/** A text/event-stream body delivered as the given byte chunks (split anywhere, even mid-UTF-8). */
export function sseBody(
	chunks: Uint8Array[],
	opts: { delayMs?: number; hold?: boolean } = {},
): ReadableStream<Uint8Array> {
	let i = 0;
	return new ReadableStream<Uint8Array>({
		async pull(controller) {
			if (opts.delayMs) await new Promise((r) => setTimeout(r, opts.delayMs));
			const next = chunks[i++];
			if (next) controller.enqueue(next);
			else if (!opts.hold) controller.close();
			else await new Promise(() => {});
		},
	});
}

export function sseResponse(events: SseEvent[], opts: { delayMs?: number; hold?: boolean; perEvent?: boolean } = {}) {
	const enc = new TextEncoder();
	const chunks = opts.perEvent === false ? [enc.encode(sseText(events))] : events.map((e) => enc.encode(sseText([e])));
	return new HttpResponse(sseBody(chunks, opts), {
		headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
	});
}

/** The stream's sources carry each passage's score breakdown (the retrieve call is gone). */
export const STREAM_SOURCES = SOURCES.map((s, i) => ({
	...s,
	score_breakdown: RETRIEVE.chunks[i]?.score_breakdown ?? null,
}));

export const SOURCES_EVENT: SseEvent = ["sources", { query_id: QUERY_ID, sources: STREAM_SOURCES }];

/** Stages as the engine sends them (api/stage_events.py); 1840 ms = 240 understand+search + 1600 write. */
export const STREAM_EVENTS: SseEvent[] = [
	["stage", { stage: "understand", elapsed_ms: 0 }],
	["stage", { stage: "search", elapsed_ms: 30 }],
	SOURCES_EVENT,
	["stage", { stage: "write", elapsed_ms: 240 }],
	...ANSWER_TOKENS.map((c): SseEvent => ["token", { content: c }]),
	[
		"done",
		{
			query_id: QUERY_ID,
			model: "openai/gpt-oss-20b",
			latency_ms: 1840,
			usage: { prompt_tokens: 900, completion_tokens: 150, total_tokens: 1050 },
			timings: { understand: 30, search: 74, write: 1600 },
			stage_timings_ms: RETRIEVE.stage_timings_ms,
			retrieval_ms: 240,
			processed_query: RETRIEVE.processed_query,
		},
	],
];

export interface LoggedRequest {
	method: string;
	path: string;
	body: Record<string, unknown> | null;
	headers: Record<string, string>;
}
export const queryLog: LoggedRequest[] = [];
export function resetQueryState(): void {
	queryLog.length = 0;
}

async function log(request: Request) {
	let body: Record<string, unknown> | null = null;
	try {
		body = (await request.clone().json()) as Record<string, unknown>;
	} catch {
		body = null;
	}
	const headers: Record<string, string> = {};
	request.headers.forEach((v, k) => {
		headers[k] = v;
	});
	queryLog.push({ method: request.method, path: new URL(request.url).pathname, body, headers });
}

export const queryHandlers = [
	http.post(`${BASE}/api/v1/query/stream`, async ({ request }) => {
		await log(request);
		return sseResponse(STREAM_EVENTS);
	}),
	http.post(`${BASE}/api/v1/query/retrieve`, async ({ request }) => {
		await log(request);
		return HttpResponse.json(RETRIEVE);
	}),
	http.post(`${BASE}/api/v1/query`, async ({ request }) => {
		await log(request);
		return HttpResponse.json(QUERY_JSON);
	}),
	http.get(`${BASE}/api/v1/query/:id`, async ({ request, params }) => {
		await log(request);
		return HttpResponse.json({
			id: String(params.id),
			query_text: "Do I need to fast for a lipid profile test?",
			response: ANSWER,
			confidence_score: 0.88,
			latency_ms: 1840,
			model_used: "openai/gpt-oss-20b",
			token_usage: { prompt_tokens: 1000, completion_tokens: 200, total_tokens: 1200 },
			created_at: "2026-09-30T10:00:00Z",
		});
	}),
	http.post(`${BASE}/api/v1/feedback`, async ({ request }) => {
		await log(request);
		return HttpResponse.json({ feedback_id: "fb-1", message: "Feedback recorded" }, { status: 201 });
	}),
];

/** A2/A6 contract body for `question_cap_reached` (429 + Retry-After). */
export function capResponse() {
	return HttpResponse.json(
		{
			detail: "Monthly question limit reached — contact support@sahayak.ai",
			code: "question_cap_reached",
			limit: 1000,
			used: 1000,
			resets_at: "2026-10-01T00:00:00Z",
		},
		{ status: 429, headers: { "Retry-After": "86400" } },
	);
}
