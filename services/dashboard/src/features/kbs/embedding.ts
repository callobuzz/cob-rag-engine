import type { CorpusDetail, EmbeddingEntry } from "./api";
import { embeddingValue } from "./CreateKbDialog";

/** A `{provider, model}` pair from a loosely typed embedding config, or null. */
export function embeddingPair(
	c: Record<string, unknown> | null | undefined,
): { provider: string; model: string } | null {
	if (!c || typeof c.provider !== "string" || typeof c.model !== "string") return null;
	return { provider: c.provider, model: c.model };
}

/**
 * The embedding model a knowledge base really uses: its own choice, else the engine default from
 * `GET /api/v1/info` (a knowledge base on the default has `embedding_config: null`).
 */
export function effectiveEmbedding(
	kb: Pick<CorpusDetail, "embedding_config">,
	info: { embedding: { provider: string; model: string } } | undefined,
): { provider: string; model: string } | null {
	const configured = embeddingPair(kb.embedding_config);
	const engine = info ? { provider: info.embedding.provider, model: info.embedding.model } : null;
	return configured ?? engine;
}

/** Whether the catalog documents this model as handling Hindi and other Indian languages. */
export function isMultilingual(m: EmbeddingEntry): boolean {
	return m.multilingual === true;
}

/** The catalog entry for a model: `undefined` when this engine's catalog does not list it. */
export function catalogEntry(
	catalog: readonly EmbeddingEntry[] | undefined,
	model: { provider: string; model: string } | null,
): EmbeddingEntry | undefined {
	if (!model) return undefined;
	const value = embeddingValue(model.provider, model.model);
	return (catalog ?? []).find((m) => embeddingValue(m.provider, m.model) === value);
}
