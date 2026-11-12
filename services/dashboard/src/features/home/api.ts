import { queryOptions } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

export type Home = components["schemas"]["HomeOut"];
export type HomeSite = components["schemas"]["HomeSite"];

/**
 * `GET /api/v1/home`: the business's assistant, website and today's numbers. `kb` names a
 * knowledge base; left out, the engine answers for the workspace's main one (its oldest).
 * Refreshed every minute while the page is open so "waiting for a call back" stays honest.
 */
export const homeQuery = (kb?: string) =>
	queryOptions({
		queryKey: qk.home(kb),
		queryFn: () => unwrap(api().GET("/api/v1/home", { params: { query: kb ? { corpus_id: kb } : {} } })),
		refetchInterval: 60_000,
		staleTime: 30_000,
	});
