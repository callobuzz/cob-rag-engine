import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { TopUpRequestBody } from "@/lib/api/billing-types";
import { api, unwrap } from "@/lib/api/client";

/**
 * Statements (BIL6): the owner's own (`/billing/statements`) and, for the super admin, any
 * customer's (`/admin/billing/accounts/{id}/statements`). The page and the CSV are fetched as
 * text with the session's credentials, then shown or saved by the browser; there is no link to
 * a file because the engine wants a credential on every request.
 */
export const statementKeys = {
	own: ["billing", "statements", "me"] as const,
	admin: (tenantId: string) => ["billing", "statements", tenantId] as const,
};

export const adminStatementsQuery = (tenantId: string) =>
	queryOptions({
		queryKey: statementKeys.admin(tenantId),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/admin/billing/accounts/{tenant_id}/statements", {
					params: { path: { tenant_id: tenantId } },
				}),
			),
	});

/** One month as the page ("html", for viewing and printing) or the spreadsheet ("csv"). */
export async function loadOwnStatement(month: string, format: "csv" | "html"): Promise<string> {
	return (await unwrap(
		api().GET("/api/v1/billing/statements/{month}", {
			params: { path: { month }, query: { format } },
			parseAs: "text",
		}),
	)) as unknown as string;
}

export async function loadAdminStatement(tenantId: string, month: string, format: "csv" | "html"): Promise<string> {
	return (await unwrap(
		api().GET("/api/v1/admin/billing/accounts/{tenant_id}/statements/{month}", {
			params: { path: { tenant_id: tenantId, month }, query: { format } },
			parseAs: "text",
		}),
	)) as unknown as string;
}

export function useRequestTopUp() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: TopUpRequestBody) => unwrap(api().POST("/api/v1/billing/top-up-requests", { body })),
		onSuccess: () => qc.invalidateQueries({ queryKey: ["billing", "account", "me"] }),
	});
}
