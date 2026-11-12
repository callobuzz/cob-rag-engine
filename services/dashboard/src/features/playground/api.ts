import { useMutation, useQueryClient } from "@tanstack/react-query";
import { authFetch } from "@/lib/api/auth-fetch";
import { api, unwrap } from "@/lib/api/client";
import { ApiError, isApiError, parseApiError, toApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { readSse } from "@/lib/api/sse";
import { currentRagUrl } from "@/lib/auth/store";

/**
 * Playground calls (D5). The answer streams from `POST /query/stream` (named events `stage`,
 * `sources`, `token`, `done`, `error`; api/routes/query.py) through `authFetch` + `readSse`. The
 * stream also carries what `POST /query/retrieve` used to be called for in parallel — each
 * source's `score_breakdown` and the stage timings in `done` — so one question is one engine
 * call. `retrieve` remains only for answers that are not streamed (answer check on, streaming
 * off) and for Compare. Confidence and exact tokens come from `GET /query/{id}` after `done`.
 * BYOK `X-LLM-*` headers are passed per call from memory only.
 */
type S = components["schemas"];
export type QueryOptions = S["QueryOptions"];
export type QueryResponse = S["QueryResponse"];
export type RetrieveResponse = S["RetrieveResponse"];
export type RetrievedChunk = S["RetrievedChunk"];
export type ScoreBreakdown = S["ScoreBreakdown"];
export type QueryHistory = S["callobuzz_rag__api__schemas__query__QueryHistoryResponse"];

/** One citation of the stream's `sources` event (`_stream_source` in query.py). */
export interface StreamSource {
	index: number;
	chunk_id: string;
	document_id: string | null;
	external_id: string | null;
	file_name: string | null;
	section: string | null;
	page: number | null;
	snippet: string;
	score: number;
	/** Per-signal detail behind `score`; null when the pipeline has none (or the engine is older). */
	score_breakdown?: ScoreBreakdown | null;
}

export interface StreamDone {
	query_id: string;
	model: string;
	latency_ms: number;
	usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number; reasoning_tokens?: number };
	/** ms per stage (understand, search, rerank when on, write). */
	timings?: Record<string, number>;
	/** The engine's finer steps, the keys of `/query/retrieve`'s `stage_timings_ms`. */
	stage_timings_ms?: Record<string, number>;
	retrieval_ms?: number;
	processed_query?: string;
	thinking_ms?: number;
	reasoning_tokens?: number;
	/** The assistant's fields (conversation layer); absent when it is switched off. */
	label?: string;
	route?: string;
	language?: string;
	cta?: { type: string; label: string; url: string | null } | null;
	claims_removed?: number;
	gate_threshold?: number | null;
}

/** The first event of an assistant-answered stream. */
export interface StreamMeta {
	query_id: string;
	conversation_id: string | null;
	session: string | null;
	label: string;
	route: string;
	language: string;
	script: string;
	assistant_version: number | null;
}

export interface StreamQuickReply {
	label: string;
	message: string;
}

/** The assistant options of a preview turn (`QueryRequest.assistant`). */
export type AssistantRequestOptions = S["AssistantRequestOptions"];

/** A model for this question only (X-LLM-* headers). Empty fields are not sent. */
export interface LlmOverride {
	provider: string;
	model: string;
	apiKey: string;
	baseUrl: string;
}

export const EMPTY_OVERRIDE: LlmOverride = { provider: "", model: "", apiKey: "", baseUrl: "" };

export function llmHeaders(o: LlmOverride | null | undefined): Record<string, string> {
	const h: Record<string, string> = {};
	if (!o) return h;
	if (o.provider.trim()) h["X-LLM-Provider"] = o.provider.trim();
	if (o.model.trim()) h["X-LLM-Model"] = o.model.trim();
	if (o.apiKey.trim()) h["X-LLM-API-Key"] = o.apiKey.trim();
	if (o.baseUrl.trim()) h["X-LLM-Base-URL"] = o.baseUrl.trim();
	return h;
}

export interface AskInput {
	kb: string;
	query: string;
	/** Omitted = the knowledge base's saved settings. */
	options?: QueryOptions;
	systemPrompt?: string;
	llm?: LlmOverride | null;
	/** The conversation layer's options (preview: draft, trace, simulation). Omitted = none. */
	assistant?: AssistantRequestOptions;
	signal?: AbortSignal;
}

/** `/query/retrieve` has no `assistant` option; the answering routes do. */
function body(input: AskInput) {
	return {
		query: input.query,
		corpus_id: input.kb,
		...(input.options && Object.keys(input.options).length ? { options: input.options } : {}),
		...(input.systemPrompt?.trim() ? { system_prompt: input.systemPrompt.trim() } : {}),
	};
}

function answerBody(input: AskInput) {
	return { ...body(input), ...(input.assistant ? { assistant: input.assistant } : {}) };
}

/** 403 "Streaming is disabled by configuration" (RAG_QUERY_STREAM_ENABLED=false). */
export function isStreamDisabled(e: unknown): boolean {
	return isApiError(e) && e.status === 403 && /stream/i.test(e.detail) && /disabled/i.test(e.detail);
}

export function isAbort(e: unknown): boolean {
	return (
		(e instanceof DOMException && e.name === "AbortError") ||
		(isApiError(e) && e.code === "aborted") ||
		(e instanceof Error && e.name === "AbortError")
	);
}

export interface StreamHandlers {
	/** Live progress: the engine started `stage` (`elapsedMs` since the stream began). */
	onStage?: (stage: string, elapsedMs: number) => void;
	onSources: (queryId: string, sources: StreamSource[]) => void;
	onToken: (text: string) => void;
	onDone: (done: StreamDone) => void;
	/** The assistant's first event: turn type, route, language. */
	onMeta?: (meta: StreamMeta) => void;
	onQuickReplies?: (items: StreamQuickReply[]) => void;
	/** A lead form or a talk-to-a-person card (`kind`); other kinds are not passed on. */
	onUi?: (block: Record<string, unknown> & { kind: "lead_form" | "handoff_card" }) => void;
	/** Owner debug trace (`assistant.debug`); the caller types it. */
	onTrace?: (trace: Record<string, unknown>) => void;
	/** Mid-stream `error` event: the partial answer stays. `code` is set for a failure before the first source. */
	onError: (detail: string, code?: string) => void;
}

/**
 * POST /api/v1/query/stream. A failure before streaming starts rejects with an `ApiError`
 * (429 question_cap_reached, 403 stream disabled, 422, 5xx…); mid-stream failures arrive as
 * `onError`. Resolves when the stream ends (or is aborted).
 */
export async function streamAnswer(input: AskInput, handlers: StreamHandlers): Promise<void> {
	let response: Response;
	try {
		response = await authFetch(`${currentRagUrl()}/api/v1/query/stream`, {
			method: "POST",
			headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...llmHeaders(input.llm) },
			body: JSON.stringify(answerBody(input)),
			signal: input.signal,
		});
	} catch (e) {
		throw toApiError(e);
	}
	if (!response.ok) throw await parseApiError(response);
	if (!response.body) throw new ApiError({ status: 0, code: "network", detail: "The answer stream was empty." });
	let ended = false;
	try {
		for await (const ev of readSse(response.body, input.signal)) {
			const data = parseJson(ev.data);
			if (ev.event === "stage") {
				if (typeof data.stage === "string") {
					handlers.onStage?.(data.stage, typeof data.elapsed_ms === "number" ? data.elapsed_ms : 0);
				}
			} else if (ev.event === "sources") {
				handlers.onSources(
					String(data.query_id ?? ""),
					Array.isArray(data.sources) ? (data.sources as StreamSource[]) : [],
				);
			} else if (ev.event === "meta") {
				handlers.onMeta?.(data as unknown as StreamMeta);
			} else if (ev.event === "quick_replies") {
				handlers.onQuickReplies?.(Array.isArray(data.items) ? (data.items as StreamQuickReply[]) : []);
			} else if (ev.event === "ui") {
				if (data.kind === "lead_form" || data.kind === "handoff_card") {
					handlers.onUi?.(data as Record<string, unknown> & { kind: "lead_form" | "handoff_card" });
				}
			} else if (ev.event === "trace") {
				handlers.onTrace?.(data);
			} else if (ev.event === "token") {
				if (typeof data.content === "string") handlers.onToken(data.content);
			} else if (ev.event === "done") {
				ended = true;
				handlers.onDone(data as unknown as StreamDone);
			} else if (ev.event === "error") {
				ended = true;
				handlers.onError(
					typeof data.detail === "string" ? data.detail : "",
					typeof data.code === "string" ? data.code : undefined,
				);
			}
		}
	} catch (e) {
		if (input.signal?.aborted || isAbort(e)) return;
		throw toApiError(e);
	}
	if (!ended && !input.signal?.aborted) handlers.onError("");
}

function parseJson(text: string): Record<string, unknown> {
	try {
		const v: unknown = JSON.parse(text);
		return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
	} catch {
		return {};
	}
}

/** POST /api/v1/query (JSON): compare, and the fallback when streaming is off. */
export function askJson(input: AskInput): Promise<QueryResponse> {
	return unwrap(
		api().POST("/api/v1/query", { body: answerBody(input), headers: llmHeaders(input.llm), signal: input.signal }),
	);
}

/**
 * POST /api/v1/query/retrieve: per-signal scores and stage timings (not metered, not capped).
 * Not used for a streamed answer: the stream carries both.
 */
export function retrieve(input: AskInput): Promise<RetrieveResponse> {
	return unwrap(
		api().POST("/api/v1/query/retrieve", { body: body(input), headers: llmHeaders(input.llm), signal: input.signal }),
	);
}

/** GET /api/v1/query/{id}: confidence, exact token usage, model. */
export function getQuery(id: string, signal?: AbortSignal): Promise<QueryHistory> {
	return unwrap(api().GET("/api/v1/query/{query_id}", { params: { path: { query_id: id } }, signal }));
}

export function useSendFeedback() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (v: { queryId: string; rating: 1 | 5; correction?: string }) =>
			unwrap(
				api().POST("/api/v1/feedback", {
					body: {
						query_id: v.queryId,
						rating: v.rating,
						...(v.correction?.trim() ? { correction: v.correction.trim() } : {}),
					},
				}),
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: qk.feedback.all }),
	});
}

/** After any answer: the plan's question count and usage are stale (not refetched now). */
export function markAnswerSpent(qc: ReturnType<typeof useQueryClient>) {
	void qc.invalidateQueries({ queryKey: qk.plan.me, refetchType: "none" });
	void qc.invalidateQueries({ queryKey: qk.usage.all, refetchType: "none" });
	// The header's month figure is on screen: refetch it now, so it moves with each answer.
	void qc.invalidateQueries({ queryKey: qk.usage.consumption });
}

/** Stream `error` codes the dashboard has its own wording for (see error-messages.ts). */
const STREAM_FAILURE_CODES = ["retrieval_failed", "query_invalid"];

/**
 * An `ApiError` for a streamed failure that has a code-mapped message (shown with
 * `messageFor`), or null when the engine's own `detail` is the best text there is.
 */
export function streamFailure(detail: string, code?: string): ApiError | null {
	if (!code || !STREAM_FAILURE_CODES.includes(code)) return null;
	return new ApiError({ status: code === "query_invalid" ? 422 : 502, code, detail });
}
