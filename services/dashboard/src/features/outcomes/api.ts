import { queryOptions } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

export type Outcomes = components["schemas"]["OutcomesResponse"];

/** `corpusId` undefined = every knowledge base in the caller's scope (Insights "all"). */
export const outcomesQuery = (corpusId: string | undefined, params: { from?: string; to?: string }) =>
	queryOptions({
		queryKey: qk.outcomes.report(corpusId ?? "all", params),
		queryFn: () =>
			corpusId
				? unwrap(
						api().GET("/api/v1/corpora/{corpus_id}/assistant/analytics", {
							params: { path: { corpus_id: corpusId }, query: params },
						}),
					)
				: unwrap(api().GET("/api/v1/assistant/analytics", { params: { query: params } })),
	});
