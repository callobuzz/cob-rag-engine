import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type ReviewItem = S["ReviewItemOut"];
export type ReviewKind = ReviewItem["kind"];
export type ReviewActionBody = S["ReviewActionRequest"];

export const reviewQuery = (kb: string, params: { kind?: ReviewKind; from?: string }) =>
	queryOptions({
		queryKey: qk.review.list(kb, params),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/assistant/review", {
					params: { path: { corpus_id: kb }, query: { kind: params.kind, from: params.from } },
				}),
			),
	});

/** Review actions change the draft (answer, rule): the assistant state goes stale too. */
export function useReviewAction(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: ReviewActionBody) =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/assistant/review/actions", {
					params: { path: { corpus_id: kb } },
					body,
				}),
			),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: qk.review.all });
			void qc.invalidateQueries({ queryKey: qk.assistant.all });
		},
	});
}
