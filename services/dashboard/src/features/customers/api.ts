import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { CreateTenantRequest, UpdateTenantRequest } from "@/lib/api/accounts-types";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import { runtime } from "@/lib/auth/runtime";

/**
 * Customers (A4 lifecycle, A6 plans; admin only). Every lifecycle action invalidates the tenant
 * lists, counts and details (`qk.tenants.*`) and the plans (their tenant counts); a delete or
 * decline also drops the knowledge-base lists (the customer's KBs are gone).
 */
export type TenantStatusTab = "pending" | "active" | "suspended";

export const CUSTOMERS_PAGE_SIZE = 25;

/** The customer lists never include the operator's own workspace (`is_operator=false`): it has its own card. */
export function tenantsQuery(params: { status: TenantStatusTab; search?: string; offset: number; limit: number }) {
	const query = { ...params, is_operator: false };
	return queryOptions({
		queryKey: qk.tenants.list(query),
		queryFn: () => unwrap(api().GET("/api/v1/admin/tenants", { params: { query } })),
		placeholderData: (prev) => prev,
	});
}

/** A tab's total from `limit=1` (A4 list totals), customers only. */
export function tenantCountQuery(status: TenantStatusTab, search?: string) {
	return queryOptions({
		queryKey: [...qk.tenants.count(status), search ?? "", "customers"] as const,
		queryFn: async () => {
			const r = await unwrap(
				api().GET("/api/v1/admin/tenants", {
					params: { query: { status, search, is_operator: false, limit: 1, offset: 0 } },
				}),
			);
			return r.total;
		},
	});
}

export function tenantQuery(id: string) {
	return queryOptions({
		queryKey: qk.tenants.detail(id),
		queryFn: () => unwrap(api().GET("/api/v1/admin/tenants/{tenant_id}", { params: { path: { tenant_id: id } } })),
	});
}

export function tenantPlanQuery(id: string) {
	return queryOptions({
		queryKey: qk.tenants.plan(id),
		queryFn: () => unwrap(api().GET("/api/v1/admin/tenants/{tenant_id}/plan", { params: { path: { tenant_id: id } } })),
	});
}

/** A2: is e-mail set up? Decides "Email sent" vs "send this link yourself". */
export const smtpQuery = queryOptions({
	queryKey: qk.smtp,
	queryFn: () => unwrap(api().GET("/api/v1/admin/smtp")),
	staleTime: 5 * 60_000,
});

function useInvalidate() {
	const qc = useQueryClient();
	return (opts: { corpora?: boolean } = {}) =>
		Promise.all([
			qc.invalidateQueries({ queryKey: qk.tenants.all }),
			qc.invalidateQueries({ queryKey: qk.plans.all }),
			opts.corpora ? qc.invalidateQueries({ queryKey: qk.corpora.all }) : null,
		]);
}

const path = (id: string) => ({ params: { path: { tenant_id: id } } });

export function useActivateTenant() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({ id, plan_id, note }: { id: string; plan_id: string; note: string | null }) =>
			unwrap(api().POST("/api/v1/admin/tenants/{tenant_id}/activate", { ...path(id), body: { plan_id, note } })),
		onSuccess: () => invalidate(),
	});
}

export function useDeclineTenant() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({ id, reason }: { id: string; reason: string | null }) =>
			unwrap(api().POST("/api/v1/admin/tenants/{tenant_id}/decline", { ...path(id), body: { reason } })),
		onSuccess: () => invalidate({ corpora: true }),
	});
}

export function useSuspendTenant() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({ id, confirm_name, reason }: { id: string; confirm_name: string; reason: string | null }) =>
			unwrap(api().POST("/api/v1/admin/tenants/{tenant_id}/suspend", { ...path(id), body: { confirm_name, reason } })),
		onSuccess: () => invalidate(),
	});
}

export function useReactivateTenant() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (id: string) => unwrap(api().POST("/api/v1/admin/tenants/{tenant_id}/reactivate", path(id))),
		onSuccess: () => invalidate(),
	});
}

export function useSetTenantPlan() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({
			id,
			plan_id,
			business_agreed = false,
			reason,
		}: {
			id: string;
			plan_id: string;
			business_agreed?: boolean; // TV3 notice: the business asked for a dearer plan
			reason?: string;
		}) =>
			unwrap(
				api().POST("/api/v1/admin/tenants/{tenant_id}/plan", {
					...path(id),
					// only sent when the business asked: the engine's default is "no"
					body: (business_agreed ? { plan_id, business_agreed, reason } : { plan_id }) as {
						plan_id: string;
						business_agreed: boolean;
						reason?: string;
					},
				}),
			),
		onSuccess: () => invalidate(),
	});
}

export function useDeleteTenant() {
	const qc = useQueryClient();
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: ({
			id,
			confirm_name,
			confirm_operator_workspace,
		}: {
			id: string;
			confirm_name: string;
			/** Only for the operator's own workspace: the explicit second confirmation. */
			confirm_operator_workspace?: boolean;
		}) =>
			unwrap(
				api().DELETE("/api/v1/admin/tenants/{tenant_id}", {
					params: {
						path: { tenant_id: id },
						query: { confirm_name, ...(confirm_operator_workspace ? { confirm_operator_workspace: true } : {}) },
					},
				}),
			),
		onSuccess: async (_d, { id, confirm_operator_workspace }) => {
			qc.removeQueries({ queryKey: qk.tenants.detail(id) });
			await invalidate({ corpora: true });
			// The workspace is gone: refresh the session's workspace list so no switch to it is offered.
			if (confirm_operator_workspace) await runtime.reloadPrincipal().catch(() => {});
		},
	});
}

/** "Recreate my workspace": brings back the operator's own workspace after a deliberate delete (409 when it exists). */
export function useRestoreOperatorWorkspace() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: () => unwrap(api().POST("/api/v1/admin/operator-workspace/restore")),
		onSuccess: async () => {
			await invalidate({ corpora: true });
			// The session's workspace list gains it again, so "Open workspace" is offered.
			await runtime.reloadPrincipal().catch(() => {});
		},
	});
}

export function useCreateTenant() {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (body: CreateTenantRequest) => unwrap(api().POST("/api/v1/admin/tenants", { body })),
		onSuccess: () => invalidate(),
	});
}

/** Name, description and the KB-limit override (`max_corpora`: null follows the plan). Never `is_active`. */
export function useUpdateTenant(id: string) {
	const invalidate = useInvalidate();
	return useMutation({
		mutationFn: (body: Pick<UpdateTenantRequest, "name" | "description" | "max_corpora">) =>
			unwrap(api().PUT("/api/v1/admin/tenants/{tenant_id}", { ...path(id), body })),
		onSuccess: () => invalidate({ corpora: true }),
	});
}
