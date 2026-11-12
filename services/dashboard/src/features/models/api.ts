import { queryOptions } from "@tanstack/react-query";
import { embeddingCatalogQuery, presetsQuery } from "@/features/kbs/api";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

/**
 * The model catalog (`GET /models?category=`), retrieval presets, embedding catalog (all shared
 * with D3's knowledge-base tabs) and OpenRouter discovery. Any signed-in view may read them.
 */
export type ModelEntry = components["schemas"]["ModelEntry"];
export type EmbeddingEntry = components["schemas"]["EmbeddingCatalogEntry"];
export { embeddingCatalogQuery, presetsQuery };

export const MODEL_CATEGORIES = ["free", "budget", "standard", "premium"] as const;
export type ModelCategory = (typeof MODEL_CATEGORIES)[number];

/** `GET /models?category=` (all bands when omitted). */
export function catalogQuery(category?: ModelCategory) {
	return queryOptions({
		queryKey: [...qk.models.catalog, category ?? "all"] as const,
		queryFn: () => unwrap(api().GET("/api/v1/models", { params: { query: category ? { category } : {} } })),
		staleTime: 10 * 60_000,
	});
}

/**
 * `POST /models/discover/openrouter` with `X-OpenRouter-Key`. The key lives only in the caller's
 * memory for this one request: a plain call (no query or mutation cache holds it), never stored or
 * logged.
 */
export function discoverOpenRouter(key: string) {
	return unwrap(api().POST("/api/v1/models/discover/openrouter", { params: { header: { "x-openrouter-key": key } } }));
}
