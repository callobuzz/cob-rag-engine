import { useEffect, useState } from "react";
import { fill } from "@/lib/strings/en";
import { playgroundStrings } from "@/lib/strings/playground";

/**
 * Live progress of a streamed answer. The engine sends `stage` events (`{stage, elapsed_ms}`:
 * understand → search → rerank (only when a re-ranker is on) → write; the conversation layer will
 * add more names to the same event) and a `done` carrying `timings` (ms per stage), the finer
 * `stage_timings_ms`, `retrieval_ms` and — for a reasoning model — `thinking_ms` and
 * `reasoning_tokens` (api/stage_events.py, api/routes/query.py). Everything here tolerates a stage
 * name it does not know and an engine that sends none of it.
 */
const t = playgroundStrings;

/** What the engine reported when an answer finished. Every field is optional on the wire. */
export interface AnswerTimes {
	/** ms per public stage: understand, search, rerank (absent = off), write. */
	stages: Record<string, number> | null;
	/** The engine's finer steps (`stage_timings_ms`): embed, search_dense, fuse, … */
	steps: Record<string, number> | null;
	/** Time until the sources were ready (ms). */
	retrievalMs: number | null;
	/** Reasoning time before the first word, only when the model reasoned. */
	thinkingMs: number | null;
	reasoningTokens: number | null;
}

export const NO_TIMES: AnswerTimes = {
	stages: null,
	steps: null,
	retrievalMs: null,
	thinkingMs: null,
	reasoningTokens: null,
};

function numberMap(v: unknown): Record<string, number> | null {
	if (!v || typeof v !== "object" || Array.isArray(v)) return null;
	const out: Record<string, number> = {};
	for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
		if (typeof x === "number" && Number.isFinite(x)) out[k] = x;
	}
	return Object.keys(out).length ? out : null;
}

function positive(v: unknown): number | null {
	return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

/** The timing fields of a `done` event (or of a `usage` block for the reasoning tokens). */
export function timesFromDone(done: Record<string, unknown>): AnswerTimes {
	const usage = done.usage && typeof done.usage === "object" ? (done.usage as Record<string, unknown>) : {};
	return {
		stages: numberMap(done.timings),
		steps: numberMap(done.stage_timings_ms),
		retrievalMs: typeof done.retrieval_ms === "number" ? done.retrieval_ms : null,
		thinkingMs: positive(done.thinking_ms),
		reasoningTokens: positive(done.reasoning_tokens) ?? positive(usage.reasoning_tokens),
	};
}

/** The stage names the engine uses today, in the order they run. */
export const STAGE_ORDER = ["understand", "search", "rerank", "check", "write", "verify", "form", "handoff"] as const;

const LIVE_WORDS: Record<string, string> = {
	understand: t.live.understand,
	search: t.live.search,
	rerank: t.live.rerank,
	check: t.live.check,
	write: t.live.write,
	verify: t.live.verify,
	form: t.live.form,
	handoff: t.live.handoff,
};

/** A stage in plain words for the live line; a name this build does not know is "Working on it". */
export function liveStageLabel(stage: string | null | undefined): string {
	if (!stage) return t.chat.thinking;
	return LIVE_WORDS[stage] ?? t.live.working;
}

const TIME_TEMPLATES: Record<string, string> = {
	understand: t.times.understand,
	search: t.times.search,
	rerank: t.times.rerank,
	write: t.times.write,
};

/** "1.41 s" / "84 ms" for a duration. */
export function formatDuration(ms: number): string {
	return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

/** The per-stage pieces of "where the time went", known stages first in run order, then any others. */
export function stagePieces(stages: Record<string, number> | null): { key: string; text: string }[] {
	if (!stages) return [];
	const known = STAGE_ORDER.filter((k) => typeof stages[k] === "number");
	const rest = Object.keys(stages)
		.filter((k) => !(STAGE_ORDER as readonly string[]).includes(k))
		.sort();
	return [...known, ...rest].map((key) => ({
		key,
		text: TIME_TEMPLATES[key]
			? fill(TIME_TEMPLATES[key], { v: formatDuration(stages[key] as number) })
			: fill(t.times.other, { stage: key, v: formatDuration(stages[key] as number) }),
	}));
}

/** The token count shown while an answer streams: the engine's own estimate (about 4 characters each). */
export function estimateTokens(text: string): number {
	return Math.ceil(text.length / 4);
}

/**
 * Milliseconds since `startedAt`, re-rendered every `intervalMs` while `running`. Stops (and keeps
 * its last value) when `running` turns false, so a finished answer's clock stops with it.
 */
export function useElapsed(startedAt: number, running: boolean, intervalMs = 100): number {
	const [now, setNow] = useState(() => Date.now());
	useEffect(() => {
		if (!running) return;
		setNow(Date.now());
		const id = setInterval(() => setNow(Date.now()), intervalMs);
		return () => clearInterval(id);
	}, [running, intervalMs]);
	return Math.max(0, now - startedAt);
}
