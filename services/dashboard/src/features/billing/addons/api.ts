import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { tenantPlanQuery } from "@/features/customers/api";
import type { ApplyAddOnRequest, CreateAddOnRequest, UpdateAddOnRequest } from "@/lib/api/billing-types";
import { api, unwrap } from "@/lib/api/client";
import { billingKeys } from "../api";

/**
 * Add-ons (BIL5, admin only): the catalogue and what each customer was given. Keys sit under the
 * billing root, so applying or revoking one also refreshes the customer's balance and, through
 * `tenantPlanQuery`, the plan limits their Overview shows.
 */
export const addOnKeys = {
	list: (includeInactive: boolean) => ["billing", "add-ons", { includeInactive }] as const,
	grants: (tenantId: string) => ["billing", "grants", tenantId] as const,
};

export const addOnsQuery = (includeInactive: boolean) =>
	queryOptions({
		queryKey: addOnKeys.list(includeInactive),
		queryFn: () =>
			unwrap(api().GET("/api/v1/admin/billing/add-ons", { params: { query: { include_inactive: includeInactive } } })),
	});

export const grantsQuery = (tenantId: string) =>
	queryOptions({
		queryKey: addOnKeys.grants(tenantId),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/admin/billing/accounts/{tenant_id}/grants", {
					params: { path: { tenant_id: tenantId } },
				}),
			),
	});

export function useCreateAddOn() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: CreateAddOnRequest) => unwrap(api().POST("/api/v1/admin/billing/add-ons", { body })),
		onSuccess: () => qc.invalidateQueries({ queryKey: billingKeys.all }),
	});
}

export function useUpdateAddOn() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, body }: { id: string; body: UpdateAddOnRequest }) =>
			unwrap(api().PATCH("/api/v1/admin/billing/add-ons/{add_on_id}", { params: { path: { add_on_id: id } }, body })),
		onSuccess: () => qc.invalidateQueries({ queryKey: billingKeys.all }),
	});
}

export function useDeleteAddOn() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(api().DELETE("/api/v1/admin/billing/add-ons/{add_on_id}", { params: { path: { add_on_id: id } } })),
		onSuccess: () => qc.invalidateQueries({ queryKey: billingKeys.all }),
	});
}

export function useApplyAddOn(tenantId: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: ApplyAddOnRequest) =>
			unwrap(
				api().POST("/api/v1/admin/billing/accounts/{tenant_id}/grants", {
					params: { path: { tenant_id: tenantId } },
					body,
				}),
			),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: billingKeys.all });
			void qc.invalidateQueries({ queryKey: tenantPlanQuery(tenantId).queryKey });
		},
	});
}

export function useRevokeGrant(tenantId: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ grantId, memo }: { grantId: string; memo?: string }) =>
			unwrap(
				api().POST("/api/v1/admin/billing/accounts/{tenant_id}/grants/{grant_id}/revoke", {
					params: { path: { tenant_id: tenantId, grant_id: grantId } },
					body: memo ? { memo } : {},
				}),
			),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: billingKeys.all });
			void qc.invalidateQueries({ queryKey: tenantPlanQuery(tenantId).queryKey });
		},
	});
}
