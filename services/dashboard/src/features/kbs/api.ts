import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

/**
 * Knowledge-base data (D3). Every tab saves through `PUT /api/v1/corpora/{id}` with only its own
 * section. Invalidations follow the spec: create/delete/move → corpora, plan, usage; update →
 * detail + lists; rotate → none (the key is never read back).
 */
type S = components["schemas"];
export type CorpusDetail = S["CorpusDetailResponse"];
export type CreateCorpusRequest = S["CreateCorpusRequest"];
export type CreateCorpusResponse = S["CreateCorpusResponse"];
export type UpdateCorpusRequest = S["UpdateCorpusRequest"];
export type ModelEntry = S["ModelEntry"];
export type EmbeddingEntry = S["EmbeddingCatalogEntry"];
export type UsageResponse = S["UsageResponse"];
export type WidgetKey = S["WidgetKeyResponse"];

export const KB_PAGE_SIZE = 25;

/** YYYY-MM-DD (UTC) `days` ago; 0 = today. */
export function utcDay(daysAgo: number, now: number = Date.now()): string {
	return new Date(now - daysAgo * 86_400_000).toISOString().slice(0, 10);
}

export function corporaQuery(params: { tenant_id?: string; offset?: number; limit?: number }) {
	return queryOptions({
		queryKey: qk.corpora.list(params),
		queryFn: () => unwrap(api().GET("/api/v1/corpora", { params: { query: params } })),
		placeholderData: (prev) => prev,
	});
}

export function corpusQuery(id: string) {
	return queryOptions({
		queryKey: qk.corpora.detail(id),
		queryFn: () => unwrap(api().GET("/api/v1/corpora/{corpus_id}", { params: { path: { corpus_id: id } } })),
	});
}

export const presetsQuery = queryOptions({
	queryKey: qk.models.presets,
	queryFn: () => unwrap(api().GET("/api/v1/models/presets")),
	staleTime: 10 * 60_000,
});

export const modelCatalogQuery = queryOptions({
	queryKey: qk.models.catalog,
	queryFn: () => unwrap(api().GET("/api/v1/models")),
	staleTime: 10 * 60_000,
});

export const embeddingCatalogQuery = queryOptions({
	queryKey: qk.models.embeddings,
	queryFn: () => unwrap(api().GET("/api/v1/models/embeddings")),
	staleTime: 10 * 60_000,
});

type UsageParams = {
	corpus_id?: string;
	tenant_id?: string;
	start: string;
	end: string;
	group_by: "day" | "corpus" | "source" | "tenant" | "model";
	/** Leave the operator's own workspace out (it is not a customer). */
	exclude_operator?: boolean;
	/** Only requests paid by the engine's own key (`operator`) or by the customer's own key (`byok`). */
	payer?: "operator" | "byok";
};

export function usageQuery(params: UsageParams) {
	return queryOptions({
		queryKey: qk.usage.report(params),
		queryFn: () => unwrap(api().GET("/api/v1/usage", { params: { query: params } })),
	});
}

/** Questions per corpus over the last 7 days (the list column). */
export function useKbUsage7d(tenantId?: string) {
	return useQuery(usageQuery({ group_by: "corpus", start: utcDay(6), end: utcDay(0), tenant_id: tenantId }));
}

export function trendsQuery(corpusId: string, days = 7) {
	// Rounded to the hour so the query key stays stable between renders.
	const start = new Date(Math.floor((Date.now() - days * 86_400_000) / 3_600_000) * 3_600_000).toISOString();
	const params = { corpus_id: corpusId, start, granularity: "day" };
	return queryOptions({
		queryKey: qk.analytics.trends(params),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/analytics/queries/trends", {
					params: { query: { corpus_id: corpusId, start, granularity: "day" } },
				}),
			),
	});
}

export function widgetKeysQuery(corpusId: string) {
	return queryOptions({
		queryKey: qk.widgetKeys.byCorpus(corpusId),
		queryFn: () =>
			unwrap(api().GET("/api/v1/corpora/{corpus_id}/widget-keys", { params: { path: { corpus_id: corpusId } } })),
	});
}

/** A4: active customers for the admin's pickers (first 500). */
export const activeTenantsQuery = queryOptions({
	queryKey: qk.tenants.list({ status: "active", limit: 500 }),
	queryFn: () => unwrap(api().GET("/api/v1/admin/tenants", { params: { query: { status: "active", limit: 500 } } })),
});

/**
 * Active CUSTOMERS: the same list without the operator's own workspace (`is_operator=false`).
 * Counts, "is anyone here yet?" and the customer pickers read this one.
 */
export const activeCustomersQuery = queryOptions({
	queryKey: qk.tenants.list({ status: "active", is_operator: false, limit: 500 }),
	queryFn: () =>
		unwrap(
			api().GET("/api/v1/admin/tenants", { params: { query: { status: "active", is_operator: false, limit: 500 } } }),
		),
});

/** The operator's own workspace (at most one row; none after it was deleted on purpose). */
export const operatorWorkspaceQuery = queryOptions({
	queryKey: qk.tenants.list({ is_operator: true, limit: 1 }),
	queryFn: async () => {
		const r = await unwrap(api().GET("/api/v1/admin/tenants", { params: { query: { is_operator: true, limit: 1 } } }));
		return r.items[0] ?? null;
	},
});

/** A6: the tenant's plan and usage (hidden on error or when unlimited). */
export const planMeQuery = queryOptions({
	queryKey: qk.plan.me,
	queryFn: () => unwrap(api().GET("/api/v1/tenants/me/plan")),
});

export const useCorpora = (params: Parameters<typeof corporaQuery>[0]) => useQuery(corporaQuery(params));
export const useCorpus = (id: string) => useQuery(corpusQuery(id));
export const usePresets = () => useQuery(presetsQuery);
export const useModelCatalog = () => useQuery(modelCatalogQuery);
export const useEmbeddingCatalog = () => useQuery(embeddingCatalogQuery);
export const useCorpusWidgetKeys = (id: string) => useQuery(widgetKeysQuery(id));

export function useCreateCorpus() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: CreateCorpusRequest) => unwrap(api().POST("/api/v1/corpora", { body })),
		onSuccess: () => invalidateAfterStructuralChange(qc),
	});
}

export function useUpdateCorpus(id: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: UpdateCorpusRequest) =>
			unwrap(api().PUT("/api/v1/corpora/{corpus_id}", { params: { path: { corpus_id: id } }, body })),
		onSuccess: (data, body) => {
			qc.setQueryData(qk.corpora.detail(id), data);
			if (body.tenant_id) return invalidateAfterStructuralChange(qc);
			return Promise.all([
				qc.invalidateQueries({ queryKey: qk.corpora.detail(id) }),
				qc.invalidateQueries({ queryKey: ["corpora", "list"] }),
				body.embedding_config ? qc.invalidateQueries({ queryKey: qk.migrations.all }) : null,
			]);
		},
	});
}

/**
 * `confirmName` is the typed name: a session ignores it, an API key must send it (the engine's
 * delete guardrail, 422 `confirmation_required` without it).
 */
export function useDeleteCorpus(id: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (confirmName?: string) =>
			unwrap(
				api().DELETE("/api/v1/corpora/{corpus_id}", {
					params: { path: { corpus_id: id }, query: { confirm_name: confirmName } },
				}),
			),
		onSuccess: () => {
			qc.removeQueries({ queryKey: qk.corpora.detail(id) });
			return invalidateAfterStructuralChange(qc);
		},
	});
}

function invalidateAfterStructuralChange(qc: ReturnType<typeof useQueryClient>) {
	return Promise.all([
		qc.invalidateQueries({ queryKey: qk.corpora.all }),
		qc.invalidateQueries({ queryKey: qk.plan.me }),
		qc.invalidateQueries({ queryKey: qk.usage.all }),
		qc.invalidateQueries({ queryKey: qk.tenants.all }),
	]);
}
