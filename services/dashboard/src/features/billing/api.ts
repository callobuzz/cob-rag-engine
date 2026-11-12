import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { PlanPricingRequest, UpdateBillingAccountRequest } from "@/lib/api/billing-types";
import { api, unwrap } from "@/lib/api/client";

/**
 * Billing (BIL2, admin only): plan pricing versions and customers' billing accounts. The keys live
 * here, under one root, so a pricing change refreshes the accounts that follow it.
 */
export const billingKeys = {
	all: ["billing"] as const,
	pricing: (planId: string) => ["billing", "pricing", planId] as const,
	history: (planId: string) => ["billing", "pricing", planId, "history"] as const,
	accounts: (params: { q?: string; payment_type?: string; offset?: number; limit?: number }) =>
		["billing", "accounts", params] as const,
	account: (tenantId: string) => ["billing", "account", tenantId] as const,
};

export const ACCOUNTS_PAGE_SIZE = 25;

export const planPricingQuery = (planId: string) =>
	queryOptions({
		queryKey: billingKeys.pricing(planId),
		queryFn: () =>
			unwrap(api().GET("/api/v1/admin/billing/plans/{plan_id}/pricing", { params: { path: { plan_id: planId } } })),
	});

export const pricingHistoryQuery = (planId: string) =>
	queryOptions({
		queryKey: billingKeys.history(planId),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/admin/billing/plans/{plan_id}/pricing/history", {
					params: { path: { plan_id: planId } },
				}),
			),
	});

export const billingAccountsQuery = (params: { q?: string; payment_type?: "prepaid" | "postpaid"; offset?: number }) =>
	queryOptions({
		queryKey: billingKeys.accounts({ ...params, limit: ACCOUNTS_PAGE_SIZE }),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/admin/billing/accounts", {
					params: { query: { ...params, limit: ACCOUNTS_PAGE_SIZE } },
				}),
			),
	});

export const billingAccountQuery = (tenantId: string) =>
	queryOptions({
		queryKey: billingKeys.account(tenantId),
		queryFn: () =>
			unwrap(api().GET("/api/v1/admin/billing/accounts/{tenant_id}", { params: { path: { tenant_id: tenantId } } })),
	});

export function useSavePricing() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ planId, body }: { planId: string; body: PlanPricingRequest }) =>
			unwrap(
				api().PUT("/api/v1/admin/billing/plans/{plan_id}/pricing", { params: { path: { plan_id: planId } }, body }),
			),
		// A new version changes what every customer on the plan pays: refresh all of billing.
		onSuccess: () => qc.invalidateQueries({ queryKey: billingKeys.all }),
	});
}

/** What an admin sends to change an account; `business_agreed` (TV3) is only sent when the business asked. */
export type UpdateAccountInput = Omit<UpdateBillingAccountRequest, "business_agreed"> & {
	business_agreed?: boolean;
};

export function useUpdateBillingAccount(tenantId: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (input: UpdateAccountInput) =>
			unwrap(
				api().PATCH("/api/v1/admin/billing/accounts/{tenant_id}", {
					params: { path: { tenant_id: tenantId } },
					// `business_agreed` is only sent when the business asked: the engine's default is "no"
					body: input as UpdateBillingAccountRequest,
				}),
			),
		onSuccess: (data) => {
			qc.setQueryData(billingKeys.account(tenantId), data);
			return qc.invalidateQueries({ queryKey: ["billing", "accounts"] });
		},
	});
}

// TV3 notice
/** What saving this pricing would mean, writing nothing: who would pay more, and from when. */
export function usePreviewPricing() {
	return useMutation({
		mutationFn: ({ planId, body }: { planId: string; body: PlanPricingRequest }) =>
			unwrap(
				api().POST("/api/v1/admin/billing/plans/{plan_id}/pricing/preview", {
					params: { path: { plan_id: planId } },
					body,
				}),
			),
	});
}

/** Call off the price change scheduled for a plan; the businesses are told. */
export function useCancelScheduledPricing() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (planId: string) =>
			unwrap(
				api().DELETE("/api/v1/admin/billing/plans/{plan_id}/pricing/scheduled", {
					params: { path: { plan_id: planId } },
				}),
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: billingKeys.all }),
	});
}

/** Drop the change waiting for one business's next renewal. */
export function useCancelPendingChange(tenantId: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () =>
			unwrap(
				api().DELETE("/api/v1/admin/billing/accounts/{tenant_id}/pending-change", {
					params: { path: { tenant_id: tenantId } },
				}),
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: billingKeys.all }),
	});
}
// /TV3 notice
