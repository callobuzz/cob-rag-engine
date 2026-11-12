import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { CreatePlanRequest, UpdatePlanRequest } from "@/lib/api/accounts-types";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";

/**
 * Plans (A6, admin only). Every plan edit invalidates plans and tenants (plan names, limits and
 * tenant counts show on both pages).
 */
export const plansQuery = queryOptions({
	queryKey: qk.plans.list,
	queryFn: () => unwrap(api().GET("/api/v1/admin/plans", { params: { query: { include_inactive: true } } })),
});

/**
 * The plans a customer may be put on (`include_inactive=false`): active ones, never the locked
 * Internal plan. Every plan picker (activate, change plan, add customer) reads this.
 */
export const assignablePlansQuery = queryOptions({
	queryKey: qk.plans.assignable,
	queryFn: () => unwrap(api().GET("/api/v1/admin/plans", { params: { query: { include_inactive: false } } })),
});

function useInvalidate() {
	const qc = useQueryClient();
	return () =>
		Promise.all([qc.invalidateQueries({ queryKey: qk.plans.all }), qc.invalidateQueries({ queryKey: qk.tenants.all })]);
}

export function useCreatePlan() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (body: CreatePlanRequest) => unwrap(api().POST("/api/v1/admin/plans", { body })),
		onSuccess: () => invalidate(),
	});
}

export function useUpdatePlan() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({ id, body }: { id: string; body: UpdatePlanRequest }) =>
			unwrap(api().PATCH("/api/v1/admin/plans/{plan_id}", { params: { path: { plan_id: id } }, body })),
		onSuccess: () => invalidate(),
	});
}

export function useDeletePlan() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(api().DELETE("/api/v1/admin/plans/{plan_id}", { params: { path: { plan_id: id } } })),
		onSuccess: () => invalidate(),
	});
}
