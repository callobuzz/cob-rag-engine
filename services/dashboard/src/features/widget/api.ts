import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { widgetKeysQuery } from "@/features/kbs/api";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

/**
 * Widget keys of one knowledge base (D6). Invalidations follow the spec: create/delete →
 * the KB's widget keys, the plan (A6 widget-key usage) and the KB detail (API key tab list);
 * patch/rotate → the KB's widget keys (the preview reloads when the key's data changes).
 */
type S = components["schemas"];
export type WidgetKey = S["WidgetKeyResponse"];
export type WidgetAppearance = S["WidgetAppearance"];
export type CreateWidgetKeyRequest = S["CreateWidgetKeyRequest"];
export type UpdateWidgetKeyRequest = S["UpdateWidgetKeyRequest"];

export { widgetKeysQuery };

export const useWidgetKeys = (corpusId: string) => useQuery(widgetKeysQuery(corpusId));

function useInvalidate(corpusId: string) {
	const qc = useQueryClient();
	return (structural: boolean) =>
		Promise.all([
			qc.invalidateQueries({ queryKey: qk.widgetKeys.byCorpus(corpusId) }),
			structural ? qc.invalidateQueries({ queryKey: qk.plan.me }) : null,
			structural ? qc.invalidateQueries({ queryKey: qk.corpora.detail(corpusId) }) : null,
		]);
}

/** Replace one key in the cached list right away, so the card and preview update without a wait. */
function usePutInCache(corpusId: string) {
	const qc = useQueryClient();
	return (key: WidgetKey) =>
		qc.setQueryData<WidgetKey[]>(qk.widgetKeys.byCorpus(corpusId), (old) =>
			old ? (old.some((k) => k.id === key.id) ? old.map((k) => (k.id === key.id ? key : k)) : [...old, key]) : old,
		);
}

export function useCreateWidgetKey(corpusId: string) {
	const invalidate = useInvalidate(corpusId);
	const put = usePutInCache(corpusId);
	return useMutation({
		mutationFn: (body: CreateWidgetKeyRequest) =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/widget-keys", { params: { path: { corpus_id: corpusId } }, body }),
			),
		onSuccess: (key) => {
			put(key);
			return invalidate(true);
		},
	});
}

export function useUpdateWidgetKey(corpusId: string) {
	const invalidate = useInvalidate(corpusId);
	const put = usePutInCache(corpusId);
	return useMutation({
		mutationFn: ({ id, body }: { id: string; body: UpdateWidgetKeyRequest }) =>
			unwrap(
				api().PATCH("/api/v1/corpora/{corpus_id}/widget-keys/{key_id}", {
					params: { path: { corpus_id: corpusId, key_id: id } },
					body,
				}),
			),
		onSuccess: (key) => {
			put(key);
			return invalidate(false);
		},
	});
}

export function useRotateWidgetKey(corpusId: string) {
	const invalidate = useInvalidate(corpusId);
	const put = usePutInCache(corpusId);
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/widget-keys/{key_id}/rotate", {
					params: { path: { corpus_id: corpusId, key_id: id } },
				}),
			),
		onSuccess: (key) => {
			put(key);
			return invalidate(false);
		},
	});
}

export function useDeleteWidgetKey(corpusId: string) {
	const qc = useQueryClient();
	const invalidate = useInvalidate(corpusId);
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(
				api().DELETE("/api/v1/corpora/{corpus_id}/widget-keys/{key_id}", {
					params: { path: { corpus_id: corpusId, key_id: id } },
				}),
			),
		onSuccess: (_data, id) => {
			qc.setQueryData<WidgetKey[]>(qk.widgetKeys.byCorpus(corpusId), (old) => old?.filter((k) => k.id !== id));
			return invalidate(true);
		},
	});
}
