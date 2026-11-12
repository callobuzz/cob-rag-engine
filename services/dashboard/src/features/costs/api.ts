import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

/** The admin Costs page (`GET /api/v1/admin/costs`, super admin only) and its CSV. */
export type CostsResponse = components["schemas"]["CostsResponse"];
export type CostRow = components["schemas"]["UsageRow"];
export type CostGroup = "tenant" | "corpus" | "model" | "source" | "payer";

export const COST_GROUPS: CostGroup[] = ["tenant", "corpus", "model", "source", "payer"];

export interface CostsParams {
	start: string;
	end: string;
}

export function costsQuery(p: CostsParams) {
	return queryOptions({
		queryKey: qk.usage.costs({ start: p.start, end: p.end }),
		queryFn: () => unwrap(api().GET("/api/v1/admin/costs", { params: { query: p } })),
		placeholderData: keepPreviousData,
		refetchInterval: 60_000,
	});
}

/** The range as the engine's CSV (a line per usage row, with both currencies), as text. */
export function fetchCostsCsv(p: CostsParams): Promise<string> {
	return unwrap(api().GET("/api/v1/admin/costs/export", { params: { query: p }, parseAs: "text" })) as Promise<string>;
}
