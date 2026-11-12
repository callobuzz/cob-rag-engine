import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type FairUseAdminView = S["FairUseAdminView"];
export type FairUseOwnerView = S["FairUseOwnerView"];
export type FairUseRow = S["FairUseRow"];
export type FairUseState = FairUseRow["state"];
export type MetricUsage = S["MetricUsageOut"];
export type OwnerMetric = S["OwnerMetric"];
export type OverridesBody = S["FairUseOverridesRequest"];

/** `GET /admin/fair-use`: businesses with the state the engine last stored, the most worrying first. */
export function fairUseListQuery(
	params: { q?: string; state?: FairUseState; ids?: string[]; offset?: number; limit?: number } = {},
) {
	const query = { limit: 100, ...params };
	return queryOptions({
		queryKey: qk.fairUse.list(query),
		queryFn: () => unwrap(api().GET("/api/v1/admin/fair-use", { params: { query } })),
	});
}

/** One business: effective limits and policy, which are its own, usage and today's decisions. */
export const fairUseTenantQuery = (tenantId: string) =>
	queryOptions({
		queryKey: qk.fairUse.tenant(tenantId),
		queryFn: () =>
			unwrap(api().GET("/api/v1/admin/fair-use/tenants/{tenant_id}", { params: { path: { tenant_id: tenantId } } })),
	});

/** The owner's own view (read-only; AI usage as a share only). */
export const fairUseMineQuery = queryOptions({
	queryKey: qk.fairUse.mine,
	queryFn: () => unwrap(api().GET("/api/v1/fair-use")),
	staleTime: 60_000,
});

export function useSetFairUse(tenantId: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: OverridesBody) =>
			unwrap(
				api().PUT("/api/v1/admin/fair-use/tenants/{tenant_id}", {
					params: { path: { tenant_id: tenantId } },
					body,
				}),
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: qk.fairUse.all }),
	});
}
