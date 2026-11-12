import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authFetch } from "@/lib/api/auth-fetch";
import { api, unwrap } from "@/lib/api/client";
import { parseApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { currentRagUrl } from "@/lib/auth/store";

/**
 * The leads inbox (conversation C4): `GET /leads`, `GET/PATCH/DELETE /leads/{id}` and the CSV
 * export. The engine decrypts contact data per request and sends `Cache-Control: no-store`; nothing
 * here keeps a lead longer than the query cache does, and signing out clears that cache.
 */
type S = components["schemas"];
export type LeadSummary = S["LeadSummary"];
export type LeadDetail = S["LeadDetail"];
export type LeadUpdate = S["LeadUpdateRequest"];
export type LeadStatus = LeadSummary["status"];
export type LeadQualification = NonNullable<LeadSummary["qualification"]>;

export const LEADS_PAGE = 25;

export interface LeadParams {
	corpus_id?: string;
	status?: LeadStatus;
	qualification?: LeadQualification;
	q?: string;
	date_from?: string;
	date_to?: string;
	page_url?: string;
	offset?: number;
	limit?: number;
}

export const leadsQuery = (p: LeadParams) =>
	queryOptions({
		queryKey: qk.leads.list(p),
		queryFn: () => unwrap(api().GET("/api/v1/leads", { params: { query: p } })),
		placeholderData: (prev) => prev,
	});

export const leadQuery = (id: string) =>
	queryOptions({
		queryKey: qk.leads.detail(id),
		queryFn: () => unwrap(api().GET("/api/v1/leads/{lead_id}", { params: { path: { lead_id: id } } })),
	});

export const useLeads = (p: LeadParams) => useQuery(leadsQuery(p));
export const useLead = (id: string | undefined) => useQuery({ ...leadQuery(id ?? ""), enabled: !!id });

export function useUpdateLead(id: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: LeadUpdate) =>
			unwrap(api().PATCH("/api/v1/leads/{lead_id}", { params: { path: { lead_id: id } }, body })),
		onSuccess: (data) => {
			qc.setQueryData(qk.leads.detail(id), data);
			return qc.invalidateQueries({ queryKey: qk.leads.all });
		},
	});
}

export function useDeleteLead() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => unwrap(api().DELETE("/api/v1/leads/{lead_id}", { params: { path: { lead_id: id } } })),
		onSuccess: () => qc.invalidateQueries({ queryKey: qk.leads.all }),
	});
}

/** Saves the export the engine sends (a CSV with a byte-order mark, so Excel reads it as UTF-8). */
export async function downloadLeadsCsv(p: Omit<LeadParams, "offset" | "limit">): Promise<void> {
	const qs = new URLSearchParams(Object.entries(p).filter(([, v]) => v) as [string, string][]);
	const res = await authFetch(`${currentRagUrl()}/api/v1/leads/export.csv?${qs}`);
	if (!res.ok) throw await parseApiError(res);
	const blob = await res.blob();
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = `leads-${new Date().toISOString().slice(0, 10)}.csv`;
	a.click();
	URL.revokeObjectURL(url);
}
