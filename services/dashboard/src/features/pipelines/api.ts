import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type Profile = S["ProfileOut"];
export type ProfileConfig = S["ProfileConfig"];
export type ProfileResolved = S["ResolvedOut"];
export type CreateProfileBody = S["CreateProfileRequest"];
export type UpdateProfileBody = S["UpdateProfileRequest"];
export type LanesView = S["LanesOut"];
export type TenantPipeline = S["TenantPipelineOut"];

/** `GET /admin/pipelines`: every profile, the default first. */
export const pipelinesQuery = queryOptions({
	queryKey: qk.pipelines.list,
	queryFn: () => unwrap(api().GET("/api/v1/admin/pipelines")),
});

/** Which profile a business answers with (its plan's, its own override, and the effective one). */
export const tenantPipelineQuery = (tenantId: string) =>
	queryOptions({
		queryKey: qk.pipelines.tenant(tenantId),
		queryFn: () =>
			unwrap(api().GET("/api/v1/admin/pipelines/tenants/{tenant_id}", { params: { path: { tenant_id: tenantId } } })),
	});

/** Live answers in progress; `refetchInterval` is set by the caller so it can pause when hidden. */
export const lanesQuery = queryOptions({
	queryKey: qk.pipelines.lanes,
	queryFn: () => unwrap(api().GET("/api/v1/admin/pipelines/lanes")),
});

/** How often the live answers view reads: every 5 seconds, and not at all while the tab is hidden. */
export const LANES_POLL_MS = 5000;
export function lanesRefetchInterval(
	visibility: string = typeof document === "undefined" ? "visible" : document.visibilityState,
): number | false {
	return visibility === "hidden" ? false : LANES_POLL_MS;
}

/** A profile edit changes plan cards and customer rows too (their "answer speed" labels). */
function useInvalidate() {
	const qc = useQueryClient();
	return () =>
		Promise.all([
			qc.invalidateQueries({ queryKey: qk.pipelines.all }),
			qc.invalidateQueries({ queryKey: qk.plans.all }),
			qc.invalidateQueries({ queryKey: qk.fairUse.all }),
		]);
}

export function useCreateProfile() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (body: CreateProfileBody) => unwrap(api().POST("/api/v1/admin/pipelines", { body })),
		onSuccess: () => invalidate(),
	});
}

export function useUpdateProfile() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({ id, body }: { id: string; body: UpdateProfileBody }) =>
			unwrap(api().PATCH("/api/v1/admin/pipelines/{profile_id}", { params: { path: { profile_id: id } }, body })),
		onSuccess: () => invalidate(),
	});
}

export function useCloneProfile() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({ id, name, label }: { id: string; name: string; label?: string | null }) =>
			unwrap(
				api().POST("/api/v1/admin/pipelines/{profile_id}/clone", {
					params: { path: { profile_id: id } },
					body: { name, ...(label ? { label } : {}) },
				}),
			),
		onSuccess: () => invalidate(),
	});
}

export function useMakeDefault() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(api().POST("/api/v1/admin/pipelines/{profile_id}/default", { params: { path: { profile_id: id } } })),
		onSuccess: () => invalidate(),
	});
}

export function useDeleteProfile() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(api().DELETE("/api/v1/admin/pipelines/{profile_id}", { params: { path: { profile_id: id } } })),
		onSuccess: () => invalidate(),
	});
}

export function useSetTenantPipeline(tenantId: string) {
	const invalidate = useInvalidate();
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (profileId: string | null) =>
			unwrap(
				api().PUT("/api/v1/admin/pipelines/tenants/{tenant_id}", {
					params: { path: { tenant_id: tenantId } },
					body: { profile_id: profileId },
				}),
			),
		onSuccess: async () => {
			await invalidate();
			await qc.invalidateQueries({ queryKey: qk.pipelines.tenant(tenantId) });
		},
	});
}
