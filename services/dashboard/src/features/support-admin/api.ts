import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/** The provider's support inbox (TV3): `/api/v1/admin/support/...`. */
export type AdminSupportRequest = components["schemas"]["AdminSupportRequestOut"];
export type AdminSupportDetail = components["schemas"]["AdminSupportDetail"];
export type SupportStats = components["schemas"]["SupportStatsOut"];
export type SupportStatus = AdminSupportRequest["status"];
export type ReplyStatus = "answered" | "waiting" | "closed";

export const adminSupportKeys = {
	all: ["admin", "support"] as const,
	list: (status?: SupportStatus, q?: string) => ["admin", "support", "list", status ?? "all", q ?? ""] as const,
	one: (id: string) => ["admin", "support", "one", id] as const,
	stats: ["admin", "support", "stats"] as const,
};

export const adminSupportListQuery = (status?: SupportStatus, q?: string) =>
	queryOptions({
		queryKey: adminSupportKeys.list(status, q),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/admin/support/requests", {
					params: { query: { ...(status ? { status } : {}), ...(q ? { q } : {}), limit: 50 } },
				}),
			),
	});

export const adminSupportQuery = (id: string) =>
	queryOptions({
		queryKey: adminSupportKeys.one(id),
		queryFn: () =>
			unwrap(api().GET("/api/v1/admin/support/requests/{request_id}", { params: { path: { request_id: id } } })),
	});

export const supportStatsQuery = queryOptions({
	queryKey: adminSupportKeys.stats,
	queryFn: () => unwrap(api().GET("/api/v1/admin/support/stats", { params: { query: { days: 30 } } })),
});

export function useReplyToSupport(id: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (input: { body: string; status: ReplyStatus }) =>
			unwrap(
				api().POST("/api/v1/admin/support/requests/{request_id}/messages", {
					params: { path: { request_id: id } },
					body: input,
				}),
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: adminSupportKeys.all }),
	});
}

export function useSetSupportStatus(id: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (status: SupportStatus) =>
			unwrap(
				api().PATCH("/api/v1/admin/support/requests/{request_id}", {
					params: { path: { request_id: id } },
					body: { status },
				}),
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: adminSupportKeys.all }),
	});
}

/** `90` → "1 h 30 min", `45` → "45 min", `120` → "2 h". */
export function formatWait(
	minutes: number | null | undefined,
	s: { minutes: string; hours: string; hoursOnly: string; none: string },
): string {
	if (minutes == null) return s.none;
	if (minutes < 60) return s.minutes.replace("{n}", String(minutes));
	const h = Math.floor(minutes / 60);
	const m = minutes % 60;
	return m ? s.hours.replace("{n}", String(h)).replace("{m}", String(m)) : s.hoursOnly.replace("{n}", String(h));
}
