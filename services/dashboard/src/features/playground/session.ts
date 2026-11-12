import { useSyncExternalStore } from "react";
import type { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/api/errors";
import {
	type AskInput,
	type AssistantRequestOptions,
	askJson,
	getQuery,
	isAbort,
	isStreamDisabled,
	type LlmOverride,
	type QueryOptions,
	type QueryResponse,
	type RetrieveResponse,
	retrieve,
	type StreamDone,
	type StreamMeta,
	type StreamQuickReply,
	type StreamSource,
	streamAnswer,
	streamFailure,
} from "./api";
import { type TokenCounts, tokensFrom } from "./cost";
import { type AnswerTimes, NO_TIMES, timesFromDone } from "./progress";

/**
 * The playground's in-memory state (spec "History": the chat is in memory per KB, never
 * persisted). A module-level store, so answers keep streaming — and stay — while the person looks
 * at another page; a reload starts afresh. The question cap is remembered per workspace for the
 * rest of the session (a reload re-enables the input; the server stays the authority).
 */

export interface UserMsg {
	id: string;
	role: "user";
	text: string;
}

export interface BotMsg {
	id: string;
	role: "bot";
	question: string;
	status: "streaming" | "done" | "error" | "stopped";
	text: string;
	queryId: string | null;
	sources: StreamSource[];
	model: string | null;
	latencyMs: number | null;
	tokens: TokenCounts | null;
	/** Undefined while `GET /query/{id}` is pending; null when the engine has none. */
	confidence: number | null | undefined;
	/** Mid-stream error detail ("" = the engine gave none). */
	error: string | null;
	/** A failure before the answer started (shown with messageFor). */
	failure: ApiError | null;
	/** `Date.now()` when the question was sent: the live clock counts from here. */
	startedAt: number;
	/** The engine's latest `stage` (understand, search, rerank, write); null before the first. */
	stage: string | null;
	/** Per-stage and thinking times, filled by `done` (or by the retrieve for a non-streamed answer). */
	times: AnswerTimes;
	/** Whether each source's score breakdown has arrived (it rides the stream's `sources` event). */
	scoresState: "pending" | "ok" | "error" | "skipped";
	/** Whether the stage timings have arrived (the stream's `done`). */
	timingsState: "pending" | "ok" | "error" | "skipped";
	/** Why this answer was not streamed (stream switched off, answer check on). */
	note: "streamOff" | "validation" | null;
	feedback: "up" | "down" | null;
	/** The language the assistant answered in (stream `meta`); overrides the KB reply language. */
	metaLanguage?: string | null;
	/** The assistant's turn details (stream `meta`, `quick_replies`, `ui`, `trace`, `done`). */
	meta?: StreamMeta | null;
	quickReplies?: StreamQuickReply[];
	ui?: Record<string, unknown>[];
	trace?: Record<string, unknown> | null;
	done?: StreamDone | null;
}

export interface CapMsg {
	id: string;
	role: "cap";
	error: ApiError;
}

export type ChatMsg = UserMsg | BotMsg | CapMsg;

export interface CompareSide {
	status: "running" | "done" | "error";
	response: QueryResponse | null;
	retrieve: RetrieveResponse | null;
	retrieveState: "pending" | "ok" | "error";
	error: ApiError | null;
}

export interface CompareRun {
	id: string;
	question: string;
	a: CompareSide;
	b: CompareSide;
}

export interface KbSession {
	messages: ChatMsg[];
	compares: CompareRun[];
	/** The answer whose sources and pipeline are shown on the right. */
	activeId: string | null;
}

const EMPTY: KbSession = { messages: [], compares: [], activeId: null };
const sessions = new Map<string, KbSession>();
const controllers = new Map<string, AbortController>();
/** tenant id (or "admin") → the question_cap_reached error that paused asking. */
const caps = new Map<string, ApiError>();
const listeners = new Set<() => void>();
let version = 0;

function emit() {
	version++;
	for (const l of listeners) l();
}

function subscribe(l: () => void) {
	listeners.add(l);
	return () => listeners.delete(l);
}

export function getSession(kb: string): KbSession {
	return sessions.get(kb) ?? EMPTY;
}

function setSession(kb: string, next: KbSession) {
	sessions.set(kb, next);
	emit();
}

export function useKbSession(kb: string): KbSession {
	return useSyncExternalStore(
		subscribe,
		() => getSession(kb),
		() => getSession(kb),
	);
}

export function useCap(tenantId: string | null | undefined): ApiError | null {
	const key = tenantId ?? "";
	return useSyncExternalStore(
		subscribe,
		() => caps.get(key) ?? null,
		() => caps.get(key) ?? null,
	);
}

export function setCap(tenantId: string | null | undefined, e: ApiError) {
	caps.set(tenantId ?? "", e);
	emit();
}

export function useBusy(kb: string): boolean {
	useSyncExternalStore(
		subscribe,
		() => version,
		() => version,
	);
	return controllers.has(kb);
}

let seq = 0;
const nextId = (p: string) => `${p}-${Date.now().toString(36)}-${(++seq).toString(36)}`;

function patch(kb: string, fn: (s: KbSession) => KbSession) {
	setSession(kb, fn(getSession(kb)));
}

export function updateBot(kb: string, id: string, change: Partial<BotMsg>) {
	patch(kb, (s) => ({
		...s,
		messages: s.messages.map((m) => (m.id === id && m.role === "bot" ? { ...m, ...change } : m)),
	}));
}

function updateCompare(kb: string, id: string, side: "a" | "b", change: Partial<CompareSide>) {
	patch(kb, (s) => ({
		...s,
		compares: s.compares.map((c) => (c.id === id ? { ...c, [side]: { ...c[side], ...change } } : c)),
	}));
}

export function setActive(kb: string, id: string) {
	patch(kb, (s) => ({ ...s, activeId: id }));
}

export function clearSession(kb: string) {
	stop(kb);
	sessions.delete(kb);
	emit();
}

/** Stop cancels the stream (and the retrieve of a non-streamed answer): one AbortController. */
export function stop(kb: string) {
	const c = controllers.get(kb);
	if (!c) return;
	c.abort();
	controllers.delete(kb);
	patch(kb, (s) => ({
		...s,
		messages: s.messages.map((m) =>
			m.role === "bot" && m.status === "streaming"
				? {
						...m,
						status: "stopped",
						scoresState: m.scoresState === "pending" ? "skipped" : m.scoresState,
						timingsState: m.timingsState === "pending" ? "skipped" : m.timingsState,
					}
				: m,
		),
	}));
}

/** Test hook: forget every session and cap. */
export function resetPlaygroundSessions() {
	for (const c of controllers.values()) c.abort();
	controllers.clear();
	sessions.clear();
	caps.clear();
	emit();
}

export interface AskRequest {
	kb: string;
	tenantId: string | null | undefined;
	question: string;
	options?: QueryOptions;
	systemPrompt?: string;
	llm?: LlmOverride | null;
	/** Keep this chat under another store key (the preview console's own: `preview:<kb>`). */
	storeKey?: string;
	/** The conversation layer's options (preview: draft, trace, simulation). */
	assistant?: AssistantRequestOptions;
}

export type AskOutcome = { kind: "ok" } | { kind: "invalid"; error: ApiError } | { kind: "cap" };

/** The retrieve's per-signal breakdown attached to each source, joined by `chunk_id` (missing → null). */
export function withBreakdowns(sources: StreamSource[], r: RetrieveResponse | null): StreamSource[] {
	if (!r) return sources;
	const byChunk = new Map(r.chunks.map((c) => [c.chunk_id, c.score_breakdown ?? null]));
	return sources.map((s) => ({ ...s, score_breakdown: byChunk.get(s.chunk_id) ?? null }));
}

export function citationsToSources(r: QueryResponse): StreamSource[] {
	return (r.citations ?? []).map((c, i) => ({
		index: i + 1,
		chunk_id: c.chunk_id,
		document_id: c.document_id ?? null,
		external_id: c.external_id ?? null,
		file_name: c.file_name ?? null,
		section: c.section ?? null,
		page: c.page ?? null,
		snippet: c.text_snippet,
		score: c.score,
	}));
}

/**
 * Ask one question: one `POST /query/stream` whose events carry the stages, the sources with
 * their score breakdowns and the timings; after `done`, `GET /query/{id}` for confidence and exact
 * tokens. Only when the answer is not streamed (streaming off, answer check on) is it
 * `POST /query` with a `POST /query/retrieve` beside it for the scores. `onSpent` runs once an
 * answer is logged.
 */
export async function ask(req: AskRequest, onSpent: () => void): Promise<AskOutcome> {
	const kb = req.storeKey ?? req.kb;
	stop(kb);
	const ctl = new AbortController();
	controllers.set(kb, ctl);
	const userId = nextId("u");
	const botId = nextId("b");
	const bot: BotMsg = {
		id: botId,
		role: "bot",
		question: req.question,
		status: "streaming",
		text: "",
		queryId: null,
		sources: [],
		model: null,
		latencyMs: null,
		tokens: null,
		confidence: undefined,
		error: null,
		failure: null,
		startedAt: Date.now(),
		stage: null,
		times: NO_TIMES,
		scoresState: "pending",
		timingsState: "pending",
		note: null,
		feedback: null,
	};
	patch(kb, (s) => ({
		...s,
		messages: [...s.messages, { id: userId, role: "user", text: req.question }, bot],
		activeId: botId,
	}));
	emit();

	const input: AskInput = {
		kb: req.kb,
		assistant: req.assistant,
		query: req.question,
		options: req.options,
		systemPrompt: req.systemPrompt,
		llm: req.llm,
		signal: ctl.signal,
	};
	const viaJson = async (note: BotMsg["note"]) => {
		// Not streamed: the answer carries no score breakdown or timings, so ask the retrieval
		// endpoint for them (the one case that still searches twice).
		let retrieved: RetrieveResponse | null = null;
		retrieve(input).then(
			(r) => {
				retrieved = r;
				const bot = getBot(kb, botId);
				updateBot(kb, botId, {
					sources: withBreakdowns(bot?.sources ?? [], r),
					scoresState: "ok",
					timingsState: "ok",
					times: { ...NO_TIMES, steps: r.stage_timings_ms ?? null, retrievalMs: r.latency_ms },
				});
			},
			(e) => {
				const state = isAbort(e) || ctl.signal.aborted ? "skipped" : "error";
				updateBot(kb, botId, { scoresState: state, timingsState: state });
			},
		);
		const r = await askJson(input);
		updateBot(kb, botId, {
			status: "done",
			text: r.answer,
			queryId: r.query_id,
			sources: withBreakdowns(citationsToSources(r), retrieved),
			model: r.model_used,
			latencyMs: r.latency_ms,
			tokens: tokensFrom(r.token_usage),
			confidence: r.confidence,
			note,
		});
		onSpent();
	};

	let text = "";
	try {
		if (req.options?.enable_validation === true) {
			await viaJson("validation");
		} else {
			try {
				await streamAnswer(input, {
					onStage: (stage) => updateBot(kb, botId, { stage }),
					onMeta: (m) => updateBot(kb, botId, { metaLanguage: m.language || null, meta: m }),
					onQuickReplies: (items) => updateBot(kb, botId, { quickReplies: items }),
					onUi: (block) => {
						const bot = getBot(kb, botId);
						updateBot(kb, botId, { ui: [...(bot?.ui ?? []), block] });
					},
					onTrace: (trace) => updateBot(kb, botId, { trace }),
					onSources: (queryId, sources) => updateBot(kb, botId, { queryId, sources, scoresState: "ok" }),
					onToken: (t) => {
						text += t;
						updateBot(kb, botId, { text });
					},
					onDone: (d) => {
						updateBot(kb, botId, {
							done: d,
							status: "done",
							queryId: d.query_id || getBot(kb, botId)?.queryId || null,
							model: d.model,
							latencyMs: d.latency_ms,
							tokens: tokensFrom(d.usage),
							times: timesFromDone(d as unknown as Record<string, unknown>),
							// An engine that does not send the timings (older than this contract): say so.
							timingsState: d.stage_timings_ms ? "ok" : "error",
						});
						onSpent();
						const qid = d.query_id || getBot(kb, botId)?.queryId;
						if (qid) void fillFromHistory(kb, botId, qid);
						else updateBot(kb, botId, { confidence: null });
					},
					onError: (detail, code) =>
						updateBot(kb, botId, {
							status: "error",
							error: detail || null,
							failure: streamFailure(detail, code),
							scoresState: getBot(kb, botId)?.scoresState === "ok" ? "ok" : "skipped",
							timingsState: "skipped",
						}),
				});
				if (ctl.signal.aborted) updateBot(kb, botId, { status: "stopped" });
			} catch (e) {
				if (!isStreamDisabled(e)) throw e;
				await viaJson("streamOff");
			}
		}
		return { kind: "ok" };
	} catch (e) {
		if (ctl.signal.aborted || isAbort(e)) {
			updateBot(kb, botId, { status: "stopped" });
			return { kind: "ok" };
		}
		const err = toApiError(e);
		if (err.code === "question_cap_reached") {
			ctl.abort();
			setCap(req.tenantId, err);
			patch(kb, (s) => ({
				...s,
				messages: [...s.messages.filter((m) => m.id !== botId), { id: nextId("c"), role: "cap", error: err }],
				activeId: s.activeId === botId ? null : s.activeId,
			}));
			return { kind: "cap" };
		}
		if (err.status === 422) {
			ctl.abort();
			patch(kb, (s) => ({
				...s,
				messages: s.messages.filter((m) => m.id !== botId && m.id !== userId),
				activeId: s.activeId === botId ? null : s.activeId,
			}));
			return { kind: "invalid", error: err };
		}
		updateBot(kb, botId, { status: "error", failure: err, confidence: null });
		return { kind: "ok" };
	} finally {
		if (controllers.get(kb) === ctl) controllers.delete(kb);
		emit();
	}
}

function getBot(kb: string, id: string): BotMsg | undefined {
	const m = getSession(kb).messages.find((x) => x.id === id);
	return m?.role === "bot" ? m : undefined;
}

async function fillFromHistory(kb: string, botId: string, queryId: string) {
	try {
		const h = await getQuery(queryId);
		const exact = tokensFrom(h.token_usage);
		updateBot(kb, botId, {
			confidence: h.confidence_score ?? null,
			...(exact ? { tokens: exact } : {}),
			...(h.model_used ? { model: h.model_used } : {}),
		});
	} catch {
		// 404/403: meta without confidence.
		updateBot(kb, botId, { confidence: null });
	}
}

/**
 * Compare: `POST /query` twice — A with the saved settings (no options, no prompt, no model
 * override), B with the try settings — plus a retrieve for each side's search time and order.
 */
export async function compare(req: AskRequest, onSpent: () => void): Promise<AskOutcome> {
	const { kb } = req;
	stop(kb);
	const ctl = new AbortController();
	controllers.set(kb, ctl);
	const id = nextId("cmp");
	const side = (): CompareSide => ({
		status: "running",
		response: null,
		retrieve: null,
		retrieveState: "pending",
		error: null,
	});
	patch(kb, (s) => ({ ...s, compares: [...s.compares, { id, question: req.question, a: side(), b: side() }] }));
	const inputs: Record<"a" | "b", AskInput> = {
		a: { kb, query: req.question, signal: ctl.signal },
		b: {
			kb,
			query: req.question,
			options: req.options,
			systemPrompt: req.systemPrompt,
			llm: req.llm,
			signal: ctl.signal,
		},
	};
	let outcome: AskOutcome = { kind: "ok" };
	const run = async (k: "a" | "b") => {
		retrieve(inputs[k]).then(
			(r) => updateCompare(kb, id, k, { retrieve: r, retrieveState: "ok" }),
			() => updateCompare(kb, id, k, { retrieveState: "error" }),
		);
		try {
			const r = await askJson(inputs[k]);
			updateCompare(kb, id, k, { status: "done", response: r });
			onSpent();
		} catch (e) {
			const err = toApiError(e);
			updateCompare(kb, id, k, { status: "error", error: err });
			if (err.code === "question_cap_reached") {
				setCap(req.tenantId, err);
				outcome = { kind: "cap" };
			} else if (err.status === 422 && outcome.kind === "ok") outcome = { kind: "invalid", error: err };
		}
	};
	try {
		await Promise.all([run("a"), run("b")]);
	} finally {
		if (controllers.get(kb) === ctl) controllers.delete(kb);
		emit();
	}
	return outcome;
}
