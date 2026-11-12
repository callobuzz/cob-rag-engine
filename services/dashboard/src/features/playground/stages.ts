/**
 * Pipeline stages (D5): the engine's `stage_timings_ms` keys (retrieval/orchestrator.py) in the
 * order they run, their plain labels, and "Write" for generation. A stage missing from the
 * timings did not run ("off", drawn dashed); keys the dashboard doesn't know are shown raw.
 */
export const STAGES = [
	["query_understanding", "Understand"],
	["embed", "Embed"],
	["search_dense", "Meaning search"],
	["search_sparse", "Keyword search"],
	["fuse", "Blend"],
	["load_chunk_text", "Load text"],
	["rerank", "Re-rank"],
	["mmr", "Variety"],
	["temporal_decay", "Recency"],
	["category_weights", "Categories"],
	["assemble", "Tidy"],
] as const;

export const WRITE_KEY = "write";
export const WRITE_LABEL = "Write";

export interface PipelineCell {
	key: string;
	label: string;
	/** Milliseconds, or null when the stage did not run. */
	ms: number | null;
	slow: boolean;
}

/**
 * Cells for the Pipeline strip: known stages in order (absent → off), unknown keys after them,
 * then "Write" (generation time, when known). The slowest cell that ran is marked.
 */
export function pipelineCells(
	timings: Record<string, number> | null | undefined,
	writeMs: number | null,
): PipelineCell[] {
	const t = timings ?? {};
	const known = new Set<string>(STAGES.map(([k]) => k));
	const cells: PipelineCell[] = STAGES.map(([key, label]) => ({
		key,
		label,
		ms: typeof t[key] === "number" ? (t[key] as number) : null,
		slow: false,
	}));
	for (const [key, ms] of Object.entries(t)) {
		if (!known.has(key) && typeof ms === "number") cells.push({ key, label: key, ms, slow: false });
	}
	if (writeMs !== null) cells.push({ key: WRITE_KEY, label: WRITE_LABEL, ms: writeMs, slow: false });
	let slowest: PipelineCell | null = null;
	for (const c of cells) if (c.ms !== null && c.ms > 0 && (!slowest || c.ms > (slowest.ms ?? 0))) slowest = c;
	if (slowest) slowest.slow = true;
	return cells;
}

/** Generation time = answer latency − search latency, never negative (spec decision). */
export function generationMs(answerLatencyMs: number | null | undefined, retrieveLatencyMs: number | null | undefined) {
	if (answerLatencyMs == null || retrieveLatencyMs == null) return null;
	return Math.max(0, answerLatencyMs - retrieveLatencyMs);
}

/** "84 ms", "1.41 s". */
export function formatMs(ms: number): string {
	if (ms < 1000) return `${Math.round(ms)} ms`;
	return `${(ms / 1000).toFixed(2)} s`;
}
