import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/**
 * The value report (TV4): the owner's settings and a month's numbers (`/api/v1/value/...`), and the
 * deployer's view of one business (`/api/v1/admin/tenants/{id}/value-report`). The printable page is
 * fetched as text with the session's credentials, then shown by the browser, like a statement.
 */
export type ValueReport = components["schemas"]["ValueReportOut"];
export type ValueSettings = components["schemas"]["ValueSettingsOut"];
export type ValueSettingsBody = components["schemas"]["ValueSettingsRequest"];
export type ValueFormulaLine = components["schemas"]["FormulaLineOut"];

export const valueKeys = {
	all: ["value"] as const,
	settings: ["value", "settings"] as const,
	report: (month?: string) => ["value", "report", month ?? "current"] as const,
	reports: ["value", "reports"] as const,
	admin: (tenantId: string, month?: string) => ["value", "admin", tenantId, month ?? "current"] as const,
};

export const valueSettingsQuery = queryOptions({
	queryKey: valueKeys.settings,
	queryFn: () => unwrap(api().GET("/api/v1/value/settings")),
});

export const valueReportQuery = (month?: string) =>
	queryOptions({
		queryKey: valueKeys.report(month),
		// The route also serves the printable page, so its JSON response is typed loosely: it is a ValueReport here.
		queryFn: async () =>
			(await unwrap(api().GET("/api/v1/value/report", { params: { query: month ? { month } : {} } }))) as ValueReport,
	});

export const valueReportsQuery = queryOptions({
	queryKey: valueKeys.reports,
	queryFn: () => unwrap(api().GET("/api/v1/value/reports")),
});

export const adminValueReportQuery = (tenantId: string, month?: string) =>
	queryOptions({
		queryKey: valueKeys.admin(tenantId, month),
		queryFn: async () =>
			(await unwrap(
				api().GET("/api/v1/admin/tenants/{tenant_id}/value-report", {
					params: { path: { tenant_id: tenantId }, query: month ? { month } : {} },
				}),
			)) as ValueReport,
	});

/** Save the owner's values. The live month and the Home card change, so both are refetched. */
export function useSaveValueSettings() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: ValueSettingsBody) => unwrap(api().PUT("/api/v1/value/settings", { body })),
		onSuccess: async () => {
			await Promise.all([
				qc.invalidateQueries({ queryKey: valueKeys.all }),
				qc.invalidateQueries({ queryKey: ["home"] }),
			]);
		},
	});
}

/** One month as the printable page (the browser's print dialog saves it as a PDF). */
export async function loadValuePage(month: string): Promise<string> {
	return (await unwrap(
		api().GET("/api/v1/value/report", { params: { query: { month, format: "html" } }, parseAs: "text" }),
	)) as unknown as string;
}

export async function loadAdminValuePage(tenantId: string, month?: string): Promise<string> {
	return (await unwrap(
		api().GET("/api/v1/admin/tenants/{tenant_id}/value-report", {
			params: { path: { tenant_id: tenantId }, query: month ? { month, format: "html" } : { format: "html" } },
			parseAs: "text",
		}),
	)) as unknown as string;
}
