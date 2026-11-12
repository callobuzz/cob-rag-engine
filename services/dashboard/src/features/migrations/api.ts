import { keepPreviousData, queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { isTerminal, MIGRATION_POLL_MS, MIGRATIONS_PAGE_SIZE, type MigrationsSearch } from "./search";

/**
 * Embedding migrations (e0 B6): read by the admin and the owning tenant, acted on by the admin only.
 * Running ones poll every 5 s until a terminal status. Every action invalidates the migrations and
 * the knowledge bases (a cut-over or rollback changes a KB's applied embedding).
 */
export type Migration = components["schemas"]["MigrationResponse"];
export type MigrationAction = "approve" | "cancel" | "retry" | "rollback";

export const migrationKeys = {
	list: (p: Record<string, unknown>) => [...qk.migrations.all, "list", p] as const,
	detail: (id: string) => [...qk.migrations.all, "detail", id] as const,
	pending: [...qk.migrations.all, "pending-count"] as const,
};

export function migrationsQuery(s: MigrationsSearch) {
	const page = s.page ?? 1;
	const query = {
		...(s.kb ? { corpus_id: s.kb } : {}),
		...(s.status ? { status: s.status } : {}),
		offset: (page - 1) * MIGRATIONS_PAGE_SIZE,
		limit: MIGRATIONS_PAGE_SIZE,
	};
	return queryOptions({
		queryKey: migrationKeys.list(query),
		queryFn: () => unwrap(api().GET("/api/v1/migrations", { params: { query } })),
		placeholderData: keepPreviousData,
		refetchInterval: (q) => (q.state.data?.items.some((m) => !isTerminal(m.status)) ? MIGRATION_POLL_MS : false),
	});
}

export function migrationQuery(id: string) {
	return queryOptions({
		queryKey: migrationKeys.detail(id),
		queryFn: () => unwrap(api().GET("/api/v1/migrations/{migration_id}", { params: { path: { migration_id: id } } })),
		refetchInterval: (q) => (q.state.data && isTerminal(q.state.data.status) ? false : MIGRATION_POLL_MS),
	});
}

/** How many wait for approval (for "Approve all pending"). */
export const pendingCountQuery = queryOptions({
	queryKey: migrationKeys.pending,
	queryFn: () =>
		unwrap(api().GET("/api/v1/migrations", { params: { query: { status: "pending_approval", limit: 1 } } })),
	select: (d) => d.total,
});

function useInvalidate() {
	const qc = useQueryClient();
	return () =>
		Promise.all([
			qc.invalidateQueries({ queryKey: qk.migrations.all }),
			qc.invalidateQueries({ queryKey: qk.corpora.all }),
		]);
}

const PATHS = {
	approve: "/api/v1/migrations/{migration_id}/approve",
	cancel: "/api/v1/migrations/{migration_id}/cancel",
	retry: "/api/v1/migrations/{migration_id}/retry",
	rollback: "/api/v1/migrations/{migration_id}/rollback",
} as const;

/** 409 (wrong state) and 410 (rollback window passed) are refetched too: the row moved on. */
export function useMigrationAction() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({ id, action }: { id: string; action: MigrationAction }) =>
			unwrap(api().POST(PATHS[action], { params: { path: { migration_id: id } } })),
		onSettled: () => invalidate(),
	});
}

export function useApproveAll() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: () => unwrap(api().POST("/api/v1/admin/migrations/approve-all")),
		onSettled: () => invalidate(),
	});
}
