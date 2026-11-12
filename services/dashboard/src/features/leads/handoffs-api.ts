import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

/**
 * Handoffs (conversation C5): `GET /handoffs`, `GET/PATCH /handoffs/{id}`. Any member may mark one
 * handled; the detail carries the linked lead's contact data, so it is never cached beyond the
 * query cache (signing out clears that).
 */
type S = components["schemas"];
export type Handoff = S["HandoffResponse"];
export type HandoffDetail = S["HandoffDetailResponse"];
export type HandoffStatus = Handoff["status"];

export const HANDOFFS_PAGE = 25;

export interface HandoffParams {
	corpus_id?: string;
	status?: HandoffStatus;
	reason?: string;
	channel?: string;
	from?: string;
	to?: string;
	offset?: number;
	limit?: number;
}

export const handoffsQuery = (p: HandoffParams) =>
	queryOptions({
		queryKey: qk.handoffs.list(p),
		queryFn: () => unwrap(api().GET("/api/v1/handoffs", { params: { query: p } })),
		placeholderData: (prev) => prev,
	});

export const handoffQuery = (id: string) =>
	queryOptions({
		queryKey: qk.handoffs.detail(id),
		queryFn: () => unwrap(api().GET("/api/v1/handoffs/{handoff_id}", { params: { path: { handoff_id: id } } })),
	});

export const useHandoffs = (p: HandoffParams) => useQuery(handoffsQuery(p));
export const useHandoff = (id: string | undefined) => useQuery({ ...handoffQuery(id ?? ""), enabled: !!id });

/** How many are waiting, for the tab label. */
export const useNewHandoffCount = (corpusId: string | undefined) =>
	useQuery({ ...handoffsQuery({ corpus_id: corpusId, status: "new", limit: 1 }), select: (d) => d.total });

export function useUpdateHandoff() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (input: { id: string; status: HandoffStatus }) =>
			unwrap(
				api().PATCH("/api/v1/handoffs/{handoff_id}", {
					params: { path: { handoff_id: input.id } },
					body: { status: input.status },
				}),
			),
		onSuccess: (_d, input) => qc.invalidateQueries({ queryKey: qk.handoffs.all }).then(() => input),
	});
}

// TV2 reference
export type ReferenceLookup = S["ReferenceLookupResponse"];

/** Find the conversation a visitor quoted a reference for. Throws `reference_not_found` (404). */
export function lookupReference(ref: string) {
	return unwrap(api().GET("/api/v1/handoffs/lookup", { params: { query: { ref } } }));
}
// /TV2 reference
