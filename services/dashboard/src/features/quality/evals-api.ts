import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

/**
 * Golden sets, criteria, judge settings, runs, samples and comparisons of one knowledge base
 * (C7). The names are `kb…` where `features/quality/api.ts` already has the operator-only
 * traffic-run hooks, so the two cannot be confused.
 */
type S = components["schemas"];
export type GoldenSet = S["GoldenSetOut"];
export type GoldenSetDetail = S["GoldenSetDetail"];
export type PairFields = S["GoldenPairFields"];
export type Criterion = S["CriterionOut"];
export type EvalSettings = S["EvalSettingsOut"];
export type Estimate = S["EstimateOut"];
export type Run = S["RunSummary"];
export type RunDetail = S["RunDetail"];
export type Sample = S["SampleOut"];
export type Comparison = S["ComparisonOut"];
export type MetricSummary = S["MetricSummary"];

export const RUN_POLL_MS = 5000;
export const isTerminalRun = (r?: { status: string }) => r?.status === "completed" || r?.status === "failed";

const p = (kb: string) => ({ corpus_id: kb });

export const goldenSetsQuery = (kb: string) =>
	queryOptions({
		queryKey: qk.evals.sets(kb),
		queryFn: () => unwrap(api().GET("/api/v1/corpora/{corpus_id}/golden-sets", { params: { path: p(kb) } })),
	});

export const goldenSetQuery = (kb: string, id: string) =>
	queryOptions({
		queryKey: qk.evals.set(kb, id),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/golden-sets/{set_id}", {
					params: { path: { ...p(kb), set_id: id } },
				}),
			),
	});

export const criteriaQuery = (kb: string) =>
	queryOptions({
		queryKey: qk.evals.criteria(kb),
		queryFn: () => unwrap(api().GET("/api/v1/corpora/{corpus_id}/eval/criteria", { params: { path: p(kb) } })),
	});

export const evalSettingsQuery = (kb: string) =>
	queryOptions({
		queryKey: qk.evals.settings(kb),
		queryFn: () => unwrap(api().GET("/api/v1/corpora/{corpus_id}/eval/settings", { params: { path: p(kb) } })),
	});

export const kbEvalRunsQuery = (kb: string, params: { set_id?: string; kind?: "traffic" | "golden" } = {}) =>
	queryOptions({
		queryKey: qk.evals.runs(kb, params),
		queryFn: () =>
			unwrap(api().GET("/api/v1/corpora/{corpus_id}/eval/runs", { params: { path: p(kb), query: params } })),
		refetchInterval: (q) => (q.state.data?.items.some((r) => !isTerminalRun(r)) ? RUN_POLL_MS : false),
	});

export const kbEvalRunQuery = (kb: string, id: string) =>
	queryOptions({
		queryKey: qk.evals.run(kb, id),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/eval/runs/{run_id}", {
					params: { path: { ...p(kb), run_id: id } },
				}),
			),
		refetchInterval: (q) => (isTerminalRun(q.state.data) ? false : RUN_POLL_MS),
	});

export interface SampleFilters {
	failed_only?: boolean;
	metric?: string;
	below?: number;
	tag?: string;
	offset?: number;
}

export const samplesQuery = (kb: string, id: string, f: SampleFilters) =>
	queryOptions({
		queryKey: qk.evals.samples(kb, id, { ...f }),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/eval/runs/{run_id}/samples", {
					params: { path: { ...p(kb), run_id: id }, query: { ...f, limit: 50 } },
				}),
			),
	});

export const compareQuery = (kb: string, ids: string[]) =>
	queryOptions({
		queryKey: qk.evals.compare(kb, ids),
		queryFn: () =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/eval/compare", { params: { path: p(kb) }, body: { run_ids: ids } }),
			),
		enabled: ids.length >= 2,
	});

function useInvalidate() {
	const qc = useQueryClient();
	// Sets and questions are two views of the same rows: the question table (`["golden"]`) too.
	return () =>
		Promise.all([qc.invalidateQueries({ queryKey: qk.evals.all }), qc.invalidateQueries({ queryKey: ["golden"] })]);
}

export function useCreateGoldenSet(kb: string) {
	const done = useInvalidate();
	return useMutation({
		mutationFn: (body: { name: string; pairs?: PairFields[] }) =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/golden-sets", {
					params: { path: p(kb) },
					body: { pairs: [], ...body },
				}),
			),
		onSuccess: done,
	});
}

export function useFromTemplate(kb: string) {
	const done = useInvalidate();
	return useMutation({
		mutationFn: () =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/golden-sets/from-template", {
					params: { path: p(kb) },
					body: { template: null },
				}),
			),
		onSuccess: done,
	});
}

export function useAddPairs(kb: string, setId: string) {
	const done = useInvalidate();
	return useMutation({
		mutationFn: (pairs: PairFields[]) =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/golden-sets/{set_id}/pairs", {
					params: { path: { ...p(kb), set_id: setId } },
					body: { pairs },
				}),
			),
		onSuccess: done,
	});
}

export function useDeleteGoldenSet(kb: string) {
	const done = useInvalidate();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(
				api().DELETE("/api/v1/corpora/{corpus_id}/golden-sets/{set_id}", {
					params: { path: { ...p(kb), set_id: id } },
				}),
			),
		onSuccess: done,
	});
}

export function useEstimate(kb: string) {
	return useMutation({
		mutationFn: (body: S["EstimateRequest"]) =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/eval/estimate", { params: { path: p(kb) }, body })),
	});
}

export function useStartRun(kb: string) {
	const done = useInvalidate();
	return useMutation({
		mutationFn: (body: S["RunCreate"]) =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/eval/runs", { params: { path: p(kb) }, body })),
		onSuccess: done,
	});
}

export function useRateSample(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, rating }: { id: string; rating: Sample["rating"] }) =>
			unwrap(
				api().PATCH("/api/v1/corpora/{corpus_id}/eval/samples/{sample_id}", {
					params: { path: { ...p(kb), sample_id: id } },
					body: { rating },
				}),
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: ["evals", "samples", kb] }),
	});
}

export function useCreateCriterion(kb: string) {
	const done = useInvalidate();
	return useMutation({
		mutationFn: (body: S["CriterionIn"]) =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/eval/criteria", { params: { path: p(kb) }, body })),
		onSuccess: done,
	});
}

export function useUpdateCriterion(kb: string) {
	const done = useInvalidate();
	return useMutation({
		mutationFn: ({ id, ...body }: S["CriterionUpdate"] & { id: string }) =>
			unwrap(
				api().PATCH("/api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}", {
					params: { path: { ...p(kb), criterion_id: id } },
					body,
				}),
			),
		onSuccess: done,
	});
}

export function useDeleteCriterion(kb: string) {
	const done = useInvalidate();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(
				api().DELETE("/api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}", {
					params: { path: { ...p(kb), criterion_id: id } },
				}),
			),
		onSuccess: done,
	});
}

export function useSaveEvalSettings(kb: string) {
	const done = useInvalidate();
	return useMutation({
		mutationFn: (body: S["EvalSettingsIn"]) =>
			unwrap(api().PUT("/api/v1/corpora/{corpus_id}/eval/settings", { params: { path: p(kb) }, body })),
		onSuccess: done,
	});
}
