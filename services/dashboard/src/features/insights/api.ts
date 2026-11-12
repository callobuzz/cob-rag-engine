import { keepPreviousData, queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import type { MemoryType } from "./search";

/**
 * Insights (D9): question history, trends, confidence, gap reports, feedback and memories.
 * Invalidations follow the spec: gap run → gaps list; feedback → feedback list/stats; memory
 * writes → memories list. Keys live under `insights`, `feedback` (shared root) and `memories`.
 */
type S = components["schemas"];
export type HistoryItem = S["QueryHistoryItem"];
export type QueryDetail = S["callobuzz_rag__api__schemas__query__QueryHistoryResponse"];
export type TrendPoint = S["TrendPoint"];
export type ConfidenceBucket = S["ConfidenceBucket"];
export type GapReport = S["callobuzz_rag__api__schemas__gaps__GapReportResponse"];
export type GapCluster = S["GapCluster"];
export type LatestGapReport = S["callobuzz_rag__api__schemas__admin__GapReportResponse"];
export type FeedbackItem = S["FeedbackItem"];
export type FeedbackStats = S["FeedbackStatsResponse"];
export type Memory = S["MemoryResponse"];
export type CreateMemory = S["CreateMemoryRequest"];

export const HISTORY_PAGE_SIZE = 20;
export const GAPS_PAGE_SIZE = 20;
export const FEEDBACK_PAGE_SIZE = 20;
export const MEMORIES_PAGE_SIZE = 50;
/** Gap runs are Celery tasks (README "Data fetching": 5 s for gap runs). */
export const POLL_MS = 5000;

export const RANGES = ["7d", "30d", "90d", "all"] as const;
export type Range = (typeof RANGES)[number];
const RANGE_DAYS: Record<Exclude<Range, "all">, number> = { "7d": 7, "30d": 30, "90d": 90 };

/** Start of a period as ISO, floored to the hour so query keys stay stable while the page is open. */
export function rangeStart(range: Range, now: number = Date.now()): string | undefined {
	if (range === "all") return undefined;
	const hour = Math.floor(now / 3_600_000) * 3_600_000;
	return new Date(hour - RANGE_DAYS[range] * 86_400_000).toISOString();
}

export const isTerminal = (status: string | undefined) => status === "completed" || status === "failed";

const insightsKeys = {
	history: (p: Record<string, unknown>) => ["insights", "history", p] as const,
	trends: (p: Record<string, unknown>) => ["insights", "trends", p] as const,
	count: (p: Record<string, unknown>) => ["insights", "count", p] as const,
	confidence: (p: Record<string, unknown>) => ["insights", "confidence", p] as const,
	query: (id: string) => ["insights", "query", id] as const,
	gaps: ["insights", "gaps"] as const,
	gapList: (p: Record<string, unknown>) => ["insights", "gaps", "list", p] as const,
	gap: (id: string) => ["insights", "gaps", "report", id] as const,
	latestGap: ["insights", "gaps", "latest"] as const,
	feedbackList: (p: Record<string, unknown>) => [...qk.feedback.all, "list", p] as const,
	feedbackStats: (p: Record<string, unknown>) => [...qk.feedback.all, "stats", p] as const,
	memories: (kb: string) => ["memories", kb] as const,
	memoryList: (kb: string, p: Record<string, unknown>) => ["memories", kb, "list", p] as const,
	memory: (kb: string, id: string) => ["memories", kb, "one", id] as const,
};

export function historyQuery(p: { corpusId?: string; page: number; session?: string }) {
	return queryOptions({
		queryKey: insightsKeys.history(p),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/analytics/queries/history", {
					params: {
						query: {
							corpus_id: p.corpusId,
							session_id: p.session || undefined,
							offset: (p.page - 1) * HISTORY_PAGE_SIZE,
							limit: HISTORY_PAGE_SIZE,
						},
					},
				}),
			),
		placeholderData: keepPreviousData,
	});
}

export function queryDetailQuery(id: string) {
	return queryOptions({
		queryKey: insightsKeys.query(id),
		queryFn: () => unwrap(api().GET("/api/v1/query/{query_id}", { params: { path: { query_id: id } } })),
	});
}

export type Granularity = "hour" | "day" | "week" | "month";

export function trendsQuery(p: { corpusId?: string; start?: string; granularity: Granularity }) {
	return queryOptions({
		queryKey: insightsKeys.trends(p),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/analytics/queries/trends", {
					params: { query: { corpus_id: p.corpusId, start: p.start, granularity: p.granularity } },
				}),
			),
		placeholderData: keepPreviousData,
	});
}

export function countQuery(p: { corpusId?: string; start?: string }) {
	return queryOptions({
		queryKey: insightsKeys.count(p),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/analytics/queries/count", { params: { query: { corpus_id: p.corpusId, start: p.start } } }),
			),
	});
}

export function confidenceQuery(p: { corpusId?: string; start?: string }) {
	return queryOptions({
		queryKey: insightsKeys.confidence(p),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/analytics/queries/confidence", {
					params: { query: { corpus_id: p.corpusId, start: p.start } },
				}),
			),
		placeholderData: keepPreviousData,
	});
}

// ---- knowledge gaps ----

export function gapListQuery(p: { corpusId?: string; page: number }) {
	return queryOptions({
		queryKey: insightsKeys.gapList(p),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/analytics/gaps", {
					params: { query: { corpus_id: p.corpusId, offset: (p.page - 1) * GAPS_PAGE_SIZE, limit: GAPS_PAGE_SIZE } },
				}),
			),
		placeholderData: keepPreviousData,
		// A queued or running report in the list keeps the list fresh too.
		refetchInterval: (q) => (q.state.data?.items.some((r) => !isTerminal(r.status)) ? POLL_MS : false),
	});
}

export function gapReportQuery(id: string) {
	return queryOptions({
		queryKey: insightsKeys.gap(id),
		queryFn: () => unwrap(api().GET("/api/v1/analytics/gaps/{report_id}", { params: { path: { report_id: id } } })),
		refetchInterval: (q) => (q.state.data && isTerminal(q.state.data.status) ? false : POLL_MS),
	});
}

export const latestGapQuery = queryOptions({
	queryKey: insightsKeys.latestGap,
	queryFn: () => unwrap(api().GET("/api/v1/admin/gaps")),
});

/**
 * One knowledge base → `POST /analytics/gaps/run?corpus_id=` (owners and members); the admin with
 * "All" selected → `POST /admin/gaps/trigger` (the global report). Both return a pollable id.
 */
export function useRunGaps() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: async (p: { corpusId?: string; global: boolean }): Promise<string> => {
			if (p.global) {
				const r = await unwrap(api().POST("/api/v1/admin/gaps/trigger", { params: { query: {} } }));
				return r.gap_report_id;
			}
			const r = await unwrap(
				api().POST("/api/v1/analytics/gaps/run", { params: { query: { corpus_id: p.corpusId } } }),
			);
			return r.id;
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: insightsKeys.gaps }),
	});
}

// ---- feedback ----

export type RatingFilter = "up" | "down" | undefined;

/** 👍 = rating ≥ 4, 👎 = rating ≤ 2 (spec). */
export function ratingBounds(r: RatingFilter): { min_rating?: number; max_rating?: number } {
	if (r === "up") return { min_rating: 4 };
	if (r === "down") return { max_rating: 2 };
	return {};
}

export function feedbackListQuery(p: {
	corpusId?: string;
	rating?: RatingFilter;
	start?: string;
	page: number;
	limit?: number;
}) {
	const limit = p.limit ?? FEEDBACK_PAGE_SIZE;
	return queryOptions({
		queryKey: insightsKeys.feedbackList(p),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/feedback", {
					params: {
						query: {
							corpus_id: p.corpusId,
							start: p.start,
							...ratingBounds(p.rating),
							offset: (p.page - 1) * limit,
							limit,
						},
					},
				}),
			),
		placeholderData: keepPreviousData,
	});
}

export function feedbackStatsQuery(p: { corpusId: string; start?: string }) {
	return queryOptions({
		queryKey: insightsKeys.feedbackStats(p),
		queryFn: () =>
			unwrap(api().GET("/api/v1/feedback/stats", { params: { query: { corpus_id: p.corpusId, start: p.start } } })),
	});
}

export function useAddFeedback() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: { query_id: string; rating: number; correction?: string | null }) =>
			unwrap(api().POST("/api/v1/feedback", { body })),
		onSuccess: () => qc.invalidateQueries({ queryKey: qk.feedback.all }),
	});
}

// ---- memories ----

export function memoriesQuery(kb: string, p: { type?: MemoryType; user?: string; page: number }) {
	return queryOptions({
		queryKey: insightsKeys.memoryList(kb, p),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/memories", {
					params: {
						path: { corpus_id: kb },
						query: {
							memory_type: p.type,
							user_id: p.user || undefined,
							limit: MEMORIES_PAGE_SIZE,
							offset: (p.page - 1) * MEMORIES_PAGE_SIZE,
						},
					},
				}),
			),
		placeholderData: keepPreviousData,
	});
}

export function memoryQuery(kb: string, id: string) {
	return queryOptions({
		queryKey: insightsKeys.memory(kb, id),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/memories/{memory_id}", {
					params: { path: { corpus_id: kb, memory_id: id } },
				}),
			),
	});
}

export function useSearchMemories(kb: string) {
	return useMutation({
		mutationFn: (body: { user_id: string; query: string }) =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/memories/search", {
					params: { path: { corpus_id: kb } },
					body: { ...body, limit: 20 },
				}),
			),
	});
}

function useMemoryMutation<V, R>(kb: string, fn: (v: V) => Promise<R>) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: fn,
		onSuccess: () => qc.invalidateQueries({ queryKey: insightsKeys.memories(kb) }),
	});
}

export const useCreateMemory = (kb: string) =>
	useMemoryMutation(kb, (body: CreateMemory) =>
		unwrap(api().POST("/api/v1/corpora/{corpus_id}/memories", { params: { path: { corpus_id: kb } }, body })),
	);

export const useBulkCreateMemories = (kb: string) =>
	useMemoryMutation(kb, (memories: CreateMemory[]) =>
		unwrap(
			api().POST("/api/v1/corpora/{corpus_id}/memories/bulk", {
				params: { path: { corpus_id: kb } },
				body: { memories },
			}),
		),
	);

export const useDeleteMemory = (kb: string) =>
	useMemoryMutation(kb, (id: string) =>
		unwrap(
			api().DELETE("/api/v1/corpora/{corpus_id}/memories/{memory_id}", {
				params: { path: { corpus_id: kb, memory_id: id } },
			}),
		),
	);

/** `DELETE …/memories?memory_type&user_id` — no filter deletes every memory of the KB (typed confirm). */
export const useBulkDeleteMemories = (kb: string) =>
	useMemoryMutation(kb, (f: { type?: MemoryType; user?: string }) =>
		unwrap(
			api().DELETE("/api/v1/corpora/{corpus_id}/memories", {
				params: { path: { corpus_id: kb }, query: { memory_type: f.type, user_id: f.user || undefined } },
			}),
		),
	);

export const usePurgeMemories = (kb: string) =>
	useMemoryMutation(kb, (_: undefined) =>
		unwrap(api().DELETE("/api/v1/corpora/{corpus_id}/memories/purge", { params: { path: { corpus_id: kb } } })),
	);

export const useHistory = (p: Parameters<typeof historyQuery>[0]) => useQuery(historyQuery(p));
