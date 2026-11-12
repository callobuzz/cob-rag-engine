import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { type AuditSearch, auditQueryParams } from "./search";

/** The audit trail (A8 §1): `GET /api/v1/admin/audit`, newest first, admin only. */
export type AuditEntry = components["schemas"]["AuditEntry"];

export const auditKeys = {
	all: ["admin", "audit"] as const,
	list: (p: ReturnType<typeof auditQueryParams>) => ["admin", "audit", p] as const,
};

export function auditQuery(s: AuditSearch) {
	const params = auditQueryParams(s);
	return queryOptions({
		queryKey: auditKeys.list(params),
		queryFn: () => unwrap(api().GET("/api/v1/admin/audit", { params: { query: params } })),
		placeholderData: keepPreviousData,
	});
}

/** Customers for the filter (every status, by name). */
export const auditTenantsQuery = queryOptions({
	queryKey: [...qk.tenants.all, "audit-filter"] as const,
	queryFn: () => unwrap(api().GET("/api/v1/admin/tenants", { params: { query: { limit: 500 } } })),
	staleTime: 60_000,
});
