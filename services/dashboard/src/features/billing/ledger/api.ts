import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { LedgerEntryKind, ManualEntryRequest } from "@/lib/api/billing-types";
import { api, unwrap } from "@/lib/api/client";
import { billingKeys } from "../api";

/**
 * Transactions (BIL3, admin only): a customer's wallet entries, a manual entry, and the daily
 * check of the books. The keys sit under the billing root, so a new entry also refreshes the
 * balance shown on the accounts list and the customer's billing summary.
 */
export const LEDGER_PAGE_SIZE = 25;

export interface LedgerParams {
	entry_type?: LedgerEntryKind;
	start?: string;
	end?: string;
	offset?: number;
}

export const ledgerKeys = {
	list: (tenantId: string, params: LedgerParams) => ["billing", "ledger", tenantId, params] as const,
	reconciliation: ["billing", "reconciliation"] as const,
};

export const ledgerQuery = (tenantId: string, params: LedgerParams) =>
	queryOptions({
		queryKey: ledgerKeys.list(tenantId, params),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/admin/billing/accounts/{tenant_id}/ledger", {
					params: { path: { tenant_id: tenantId }, query: { ...params, limit: LEDGER_PAGE_SIZE } },
				}),
			),
	});

export function useAddLedgerEntry(tenantId: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: ManualEntryRequest) =>
			unwrap(
				api().POST("/api/v1/admin/billing/accounts/{tenant_id}/ledger", {
					params: { path: { tenant_id: tenantId } },
					body,
				}),
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: billingKeys.all }),
	});
}

export const reconciliationsQuery = queryOptions({
	queryKey: ledgerKeys.reconciliation,
	queryFn: () => unwrap(api().GET("/api/v1/admin/billing/reconciliation", { params: { query: { limit: 5 } } })),
});

export function useRunReconciliation() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () => unwrap(api().POST("/api/v1/admin/billing/reconciliation")),
		// It runs in the background: look again shortly.
		onSuccess: () => {
			for (const ms of [1500, 5000]) {
				setTimeout(() => void qc.invalidateQueries({ queryKey: ledgerKeys.reconciliation }), ms);
			}
		},
	});
}
