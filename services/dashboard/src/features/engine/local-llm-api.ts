import { queryOptions, useMutation } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

export type LocalLlmStatus = components["schemas"]["LocalLLMStatusOut"];
export type LocalLlmTest = components["schemas"]["LocalLLMTestOut"];

export const localLlmKeys = { status: ["admin", "local-llm"] as const };

/** `GET /admin/local-llm`: a live check; polls faster while the model is still on its way. */
export const localLlmQuery = queryOptions({
	queryKey: localLlmKeys.status,
	queryFn: () => unwrap(api().GET("/api/v1/admin/local-llm")),
	refetchInterval: (query) => {
		const state = query.state.data?.state;
		if (state === "not_configured") return false;
		return state === "ready" ? 30_000 : 5_000;
	},
});

/** `POST /admin/local-llm/test`: the model says a few words (at most once every 10 seconds). */
export function useLocalLlmTest() {
	return useMutation({
		mutationFn: () => unwrap(api().POST("/api/v1/admin/local-llm/test")),
	});
}
