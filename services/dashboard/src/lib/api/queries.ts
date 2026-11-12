import { queryOptions } from "@tanstack/react-query";
import type { EngineInfo } from "./accounts-types";
import { api, unwrap } from "./client";
import { qk } from "./query-keys";
import type { components } from "./schema";

export type Corpus = components["schemas"]["CorpusResponse"];
export type CorpusList = components["schemas"]["CorpusListResponse"];

/** GET /info: features and limits for nav gating; failure → nav without optional items. */
export const infoQuery = queryOptions({
	queryKey: qk.info,
	queryFn: () => unwrap(api().GET("/api/v1/info")) as Promise<EngineInfo>,
	staleTime: 5 * 60_000,
});

export const KB_SWITCHER_LIMIT = 500;

/** Knowledge bases for the switcher and palette (the corpora API has no name search). */
export const corporaSwitcherQuery = queryOptions({
	queryKey: qk.corpora.list({ limit: KB_SWITCHER_LIMIT }),
	queryFn: () => unwrap(api().GET("/api/v1/corpora", { params: { query: { limit: KB_SWITCHER_LIMIT } } })),
});
