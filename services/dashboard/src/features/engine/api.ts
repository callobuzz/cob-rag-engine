import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

export { healthQuery } from "@/features/overview/api";
/**
 * Engine info (`GET /info`, shared with the shell's nav gating), health (`GET /health`, shared
 * with Overview) and the engine-wide retrieval defaults (`GET/PUT /admin/config/retrieval`).
 */
export { infoQuery } from "@/lib/api/queries";

export type RetrievalDefaults = components["schemas"]["RetrievalConfigResponse"];
export type RetrievalDefaultsUpdate = components["schemas"]["UpdateRetrievalConfigRequest"];

export const engineKeys = {
	retrieval: ["admin", "config", "retrieval"] as const,
	cob: ["admin", "cob", "status"] as const,
};

export type CobStatus = components["schemas"]["CobStatusOut"];

/** COB's docs sync (`GET /admin/cob/status`); polls while a sync is queued or running. */
export const cobStatusQuery = queryOptions({
	queryKey: engineKeys.cob,
	queryFn: () => unwrap(api().GET("/api/v1/admin/cob/status")),
	refetchInterval: (query) => {
		const state = query.state.data?.sync_state;
		return state === "queued" || state === "running" ? 5000 : false;
	},
});

export function useCobResync() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () => unwrap(api().POST("/api/v1/admin/cob/resync", { body: { eval: true } })),
		// Also on error: a 503 leaves the status `failed`, and the card must show it.
		onSettled: () => qc.invalidateQueries({ queryKey: engineKeys.cob }),
	});
}

export const retrievalDefaultsQuery = queryOptions({
	queryKey: engineKeys.retrieval,
	queryFn: () => unwrap(api().GET("/api/v1/admin/config/retrieval")),
});

/**
 * Only changed fields, never `null`: the engine stores a sent null as the override and then fails
 * to read it back, so a field can be changed but not reset from here.
 */
export function useSaveRetrievalDefaults() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: RetrievalDefaultsUpdate) => unwrap(api().PUT("/api/v1/admin/config/retrieval", { body })),
		onSuccess: (data) => {
			qc.setQueryData(engineKeys.retrieval, data);
			return qc.invalidateQueries({ queryKey: qk.info });
		},
	});
}
