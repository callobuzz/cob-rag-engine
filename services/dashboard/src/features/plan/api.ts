import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query"; // TV3 spend cap
import { planMeQuery } from "@/features/kbs/api";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";

/**
 * Plan & usage (D2): `GET /tenants/me/plan` (A6, `PlanUsageResponse`; members may read it, A5),
 * `GET /tenants/me` for the activation date and status, and `GET /usage` from the first of the
 * UTC month for the per-knowledge-base breakdown.
 */
export { planMeQuery };

export const tenantMeQuery = queryOptions({
	queryKey: qk.tenants.me,
	queryFn: () => unwrap(api().GET("/api/v1/tenants/me")),
});

/** Share of each source in a knowledge base's month: "website widget 94% · API 6%". */
export function sourceSplit(rows: { key: string | null; requests: number }[]): { kind: string; share: number }[] {
	const by = new Map<string, number>();
	let total = 0;
	for (const r of rows) {
		const kind = r.key?.startsWith("widget:") ? "widget" : (r.key ?? "other");
		by.set(kind, (by.get(kind) ?? 0) + r.requests);
		total += r.requests;
	}
	if (!total) return [];
	return [...by.entries()]
		.filter(([, n]) => n > 0)
		.sort((a, b) => b[1] - a[1])
		.map(([kind, n]) => ({ kind, share: n / total }));
}

// TV3 spend cap
/** Set or remove the workspace's monthly spending cap (owners): `PUT /billing/spend-cap`. */
export function useSetSpendCap() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (capMinor: number | null) =>
			unwrap(api().PUT("/api/v1/billing/spend-cap", { body: { cap_minor: capMinor } })),
		onSuccess: () => qc.invalidateQueries({ queryKey: ["billing", "account"] }),
	});
}
// /TV3 spend cap
