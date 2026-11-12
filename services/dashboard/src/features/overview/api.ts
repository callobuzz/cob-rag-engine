import { queryOptions } from "@tanstack/react-query";
import { isTerminal } from "@/features/add-content/api";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import type { InstantRange } from "@/lib/format/range";

/**
 * Overview reads (D2). No mutations: the only write is the local "Connect an agent" checklist
 * flag. Every card has its own query so one failure stays inside its card.
 */
export type TrendsResponse = components["schemas"]["TrendsResponse"];
export type HealthResponse = components["schemas"]["HealthResponse"];
export type GapReport = components["schemas"]["callobuzz_rag__api__schemas__gaps__GapReportResponse"];
export type QueryHistoryItem = components["schemas"]["QueryHistoryItem"];

/**
 * Confidence trend for an instant range (count-weighted in the KPI). The page computes its ranges
 * once per range choice, so the key is stable between renders.
 */
export function trendsRangeQuery(r: InstantRange) {
	const params = { start: r.start, end: r.end, granularity: "day" as const };
	return queryOptions({
		queryKey: qk.analytics.trends(params),
		queryFn: () => unwrap(api().GET("/api/v1/analytics/queries/trends", { params: { query: params } })),
	});
}

/** Checklist step 3: any question logged at all. */
export const queryCountQuery = queryOptions({
	queryKey: [...qk.analytics.all, "count"] as const,
	queryFn: () => unwrap(api().GET("/api/v1/analytics/queries/count")),
});

export const recentQuestionsQuery = queryOptions({
	queryKey: [...qk.analytics.all, "history", { limit: 5 }] as const,
	queryFn: () => unwrap(api().GET("/api/v1/analytics/queries/history", { params: { query: { limit: 5 } } })),
});

/** The latest gap report (the list carries its clusters). */
export const latestGapsQuery = queryOptions({
	queryKey: [...qk.analytics.all, "gaps", { limit: 1 }] as const,
	queryFn: () => unwrap(api().GET("/api/v1/analytics/gaps", { params: { query: { limit: 1 } } })),
});

/** Public, no credential (README "API client"). Re-checked every 30 s while the page is open. */
export const healthQuery = queryOptions({
	queryKey: qk.health,
	queryFn: () => unwrap(api().GET("/api/v1/health")),
	refetchInterval: 30_000,
	retry: false,
});

/** The five newest content jobs in scope; polls while one is still moving. */
export const recentJobsQuery = queryOptions({
	queryKey: qk.ingest.jobs({ limit: 5 }),
	queryFn: () => unwrap(api().GET("/api/v1/ingest/jobs", { params: { query: { limit: 5 } } })),
	refetchInterval: (q) => (!q.state.error && q.state.data?.items.some((j) => !isTerminal(j.status)) ? 3000 : false),
});

/** Documents waiting for a near-duplicate decision (the Documents KPI note). */
export const pendingReviewCountQuery = queryOptions({
	queryKey: qk.ingest.jobs({ status: "pending_review", limit: 1 }),
	queryFn: () =>
		unwrap(api().GET("/api/v1/ingest/jobs", { params: { query: { status: "pending_review", limit: 1 } } })),
});

/** A4: sign-ups waiting (first three and the total). */
export const pendingSignupsQuery = queryOptions({
	queryKey: qk.tenants.list({ status: "pending", is_operator: false, limit: 3 }),
	queryFn: () =>
		unwrap(
			api().GET("/api/v1/admin/tenants", { params: { query: { status: "pending", is_operator: false, limit: 3 } } }),
		),
});
