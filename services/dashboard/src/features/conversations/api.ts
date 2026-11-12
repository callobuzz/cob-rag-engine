import { type InfiniteData, infiniteQueryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { authFetch } from "@/lib/api/auth-fetch";
import { api, unwrap } from "@/lib/api/client";
import { ApiError, parseApiError, toApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { readSse } from "@/lib/api/sse";
import { currentRagUrl } from "@/lib/auth/store";
import { type AnswerTimes, timesFromDone } from "../playground/progress";

/**
 * Conversations (D5): `GET/POST /corpora/{id}/conversations`, `GET/PATCH/DELETE …/{cid}`,
 * `GET/POST …/{cid}/messages`. Replies stream with `Accept: text/event-stream` — events
 * `stage {stage, elapsed_ms}`, `thinking {step}`, `tool_call {tool, params}`,
 * `tool_result {tool, summary, duration_ms}`, `token {content}`, `done {message_id, total_tokens,
 * timings?, latency_ms?, thinking_ms?, reasoning_tokens?}`, `error {detail}` (api/streaming.py,
 * api/services/streaming_service.py, api/stage_events.py).
 */
type S = components["schemas"];
export type Conversation = S["ConversationResponse"];
export type ConversationList = S["ConversationListResponse"];
export type ConversationMessage = S["MessageResponse"];
export type MessageList = S["MessageListResponse"];

export const LIST_PAGE = 50;
export const THREAD_PAGE = 50;
export const MESSAGE_MAX = 10_000;

export function conversationsQuery(kb: string) {
	return infiniteQueryOptions({
		queryKey: qk.conversations.list(kb),
		queryFn: ({ pageParam }) =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/conversations", {
					params: { path: { corpus_id: kb }, query: { limit: LIST_PAGE, offset: pageParam } },
				}),
			),
		initialPageParam: 0,
		getNextPageParam: (last) =>
			last.offset + last.items.length < last.count ? last.offset + last.items.length : undefined,
	});
}

type ThreadPage = MessageList;
type ThreadParam = { offset: number; limit: number } | null;

function listMessages(kb: string, id: string, offset: number, limit: number) {
	return unwrap(
		api().GET("/api/v1/corpora/{corpus_id}/conversations/{conversation_id}/messages", {
			params: { path: { corpus_id: kb, conversation_id: id }, query: { limit, offset } },
		}),
	);
}

/**
 * The thread, oldest first, newest page first: the API pages oldest-first, so the first load
 * reads the count and then the last page; "Load earlier" walks back from there.
 */
export function threadQuery(kb: string, id: string) {
	return infiniteQueryOptions<
		ThreadPage,
		ApiError,
		InfiniteData<ThreadPage, ThreadParam>,
		readonly unknown[],
		ThreadParam
	>({
		queryKey: qk.conversations.thread(kb, id),
		queryFn: async ({ pageParam }) => {
			if (pageParam) return listMessages(kb, id, pageParam.offset, pageParam.limit);
			const first = await listMessages(kb, id, 0, THREAD_PAGE);
			if (first.count <= THREAD_PAGE) return first;
			return listMessages(kb, id, first.count - THREAD_PAGE, THREAD_PAGE);
		},
		initialPageParam: null,
		getNextPageParam: () => undefined,
		getPreviousPageParam: (first) =>
			first.offset > 0
				? { offset: Math.max(0, first.offset - THREAD_PAGE), limit: Math.min(THREAD_PAGE, first.offset) }
				: undefined,
	});
}

type QC = ReturnType<typeof useQueryClient>;
const invalidateList = (qc: QC, kb: string) => qc.invalidateQueries({ queryKey: qk.conversations.list(kb) });

export function useCreateConversation(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/conversations", {
					params: { path: { corpus_id: kb } },
					body: {},
				}),
			),
		onSuccess: () => invalidateList(qc, kb),
	});
}

export function useRenameConversation(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (v: { id: string; title: string }) =>
			unwrap(
				api().PATCH("/api/v1/corpora/{corpus_id}/conversations/{conversation_id}", {
					params: { path: { corpus_id: kb, conversation_id: v.id } },
					body: { title: v.title },
				}),
			),
		onSettled: () => invalidateList(qc, kb),
	});
}

export function useDeleteConversation(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(
				api().DELETE("/api/v1/corpora/{corpus_id}/conversations/{conversation_id}", {
					params: { path: { corpus_id: kb, conversation_id: id } },
				}),
			),
		onSettled: (_d, _e, id) => {
			qc.removeQueries({ queryKey: qk.conversations.thread(kb, id) });
			return invalidateList(qc, kb);
		},
	});
}

export interface ToolStep {
	tool: string;
	params: Record<string, unknown>;
	summary: string | null;
	durationMs: number | null;
}

/** What the engine reported when a reply finished (all optional on the wire). */
export interface ReplyDone {
	messageId: string;
	totalTokens: number;
	times: AnswerTimes;
	/** The whole reply, engine side (ms). */
	latencyMs: number | null;
}

export interface ReplyHandlers {
	/** Live progress: the engine started `stage`. */
	onStage?: (stage: string, elapsedMs: number) => void;
	onThinking: (step: string) => void;
	onToolCall: (tool: string, params: Record<string, unknown>) => void;
	onToolResult: (tool: string, summary: string, durationMs: number) => void;
	onToken: (text: string) => void;
	onDone: (done: ReplyDone) => void;
	/** `code` is set for a failure the dashboard words itself (retrieval_failed, query_invalid). */
	onError: (detail: string, code?: string) => void;
}

/**
 * POST …/messages with `Accept: text/event-stream`. Rejects with an `ApiError` before the stream
 * starts (429 question_cap_reached, 404, 422…); later failures arrive as `onError`.
 */
export async function streamReply(
	kb: string,
	id: string,
	content: string,
	h: ReplyHandlers,
	signal?: AbortSignal,
): Promise<void> {
	let response: Response;
	try {
		response = await authFetch(`${currentRagUrl()}/api/v1/corpora/${kb}/conversations/${id}/messages`, {
			method: "POST",
			headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
			body: JSON.stringify({ content }),
			signal,
		});
	} catch (e) {
		throw toApiError(e);
	}
	if (!response.ok) throw await parseApiError(response);
	if (!response.body) throw new ApiError({ status: 0, code: "network", detail: "The reply stream was empty." });
	let ended = false;
	for await (const ev of readSse(response.body, signal)) {
		const d = parse(ev.data);
		switch (ev.event) {
			case "stage":
				if (typeof d.stage === "string") h.onStage?.(d.stage, typeof d.elapsed_ms === "number" ? d.elapsed_ms : 0);
				break;
			case "thinking":
				h.onThinking(String(d.step ?? ""));
				break;
			case "tool_call":
				h.onToolCall(String(d.tool ?? ""), (d.params as Record<string, unknown>) ?? {});
				break;
			case "tool_result":
				h.onToolResult(String(d.tool ?? ""), String(d.summary ?? ""), Number(d.duration_ms ?? 0));
				break;
			case "token":
				if (typeof d.content === "string") h.onToken(d.content);
				break;
			case "done":
				ended = true;
				h.onDone({
					messageId: String(d.message_id ?? ""),
					totalTokens: Number(d.total_tokens ?? 0),
					times: timesFromDone(d),
					latencyMs: typeof d.latency_ms === "number" ? d.latency_ms : null,
				});
				break;
			case "error":
				ended = true;
				h.onError(typeof d.detail === "string" ? d.detail : "", typeof d.code === "string" ? d.code : undefined);
				break;
		}
	}
	if (!ended && !signal?.aborted) h.onError("");
}

function parse(text: string): Record<string, unknown> {
	try {
		const v: unknown = JSON.parse(text);
		return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
	} catch {
		return {};
	}
}

/** `retrieval_context.chunks` of a stored reply (non-streamed replies carry it). */
export interface ContextChunk {
	chunk_id: string;
	text: string;
	score: number | null;
	document_id: string | null;
}

export function contextChunks(m: Pick<ConversationMessage, "retrieval_context">): ContextChunk[] {
	const chunks = m.retrieval_context?.chunks;
	if (!Array.isArray(chunks)) return [];
	return chunks
		.filter((c): c is Record<string, unknown> => !!c && typeof c === "object")
		.map((c) => ({
			chunk_id: String(c.chunk_id ?? ""),
			text: String(c.text ?? ""),
			score: typeof c.score === "number" ? c.score : null,
			document_id: c.document_id == null ? null : String(c.document_id),
		}));
}
