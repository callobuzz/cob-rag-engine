import { keepPreviousData, queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { isTerminal, POLL_MS } from "@/features/insights/api";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { upload } from "@/lib/api/upload";

/**
 * Quality (D9): golden sets (CRUD, CSV import, JSON export), evaluation and optimisation runs
 * (admin routes). Invalidations: golden writes → golden list; triggers → runs lists.
 */
type S = components["schemas"];
export type GoldenPair = S["GoldenPairResponse"];
export type EvalRunDetail = S["EvalRunDetail"];
export type OptimizationRunDetail = S["OptimizationRunDetail"];

/** `GET /admin/eval/runs` items (PaginatedResponse is untyped; shape from api/routes/admin.py). */
export interface EvalRunSummary {
	id: string;
	status: string;
	sample_size: number | null;
	corpus_id: string | null;
	created_at: string;
	completed_at: string | null;
}
/** `GET /admin/optimize/runs` items (api/routes/admin.py `_list_optimization_runs`). */
export interface OptimizationRunSummary {
	id: string;
	status: string;
	n_trials: number | null;
	corpus_id: string | null;
	created_at: string;
	completed_at: string | null;
}
interface Paged<T> {
	items: T[];
	total: number;
	offset: number;
	limit: number;
}

export const GOLDEN_PAGE_SIZE = 25;
export const RUNS_PAGE_SIZE = 20;

const keys = {
	golden: (kb: string) => ["golden", kb] as const,
	goldenList: (kb: string, page: number) => ["golden", kb, "list", page] as const,
	evals: ["evalRuns"] as const,
	evalList: (page: number) => ["evalRuns", "list", page] as const,
	evalRun: (id: string) => ["evalRuns", "run", id] as const,
	optimize: ["optimizeRuns"] as const,
	optimizeList: (page: number) => ["optimizeRuns", "list", page] as const,
	optimizeRun: (id: string) => ["optimizeRuns", "run", id] as const,
};

// ---- golden sets ----

export function goldenQuery(kb: string, page: number) {
	return queryOptions({
		queryKey: keys.goldenList(kb, page),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/golden", {
					params: { query: { corpus_id: kb, limit: GOLDEN_PAGE_SIZE, offset: (page - 1) * GOLDEN_PAGE_SIZE } },
				}),
			),
		placeholderData: keepPreviousData,
	});
}

function useGoldenMutation<V, R>(fn: (v: V) => Promise<R>, kbOf: (v: V, r: R) => string | undefined) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: fn,
		onSuccess: (r, v) => {
			const kb = kbOf(v, r);
			void qc.invalidateQueries({ queryKey: qk.evals.all });
			return qc.invalidateQueries({ queryKey: kb ? keys.golden(kb) : ["golden"] });
		},
	});
}

export const useCreateGolden = () =>
	useGoldenMutation(
		(body: S["GoldenPairCreateRequest"]) => unwrap(api().POST("/api/v1/golden", { body })),
		(v) => v.corpus_id,
	);

export const useUpdateGolden = () =>
	useGoldenMutation(
		(p: { id: string; body: S["GoldenPairUpdateRequest"] }) =>
			unwrap(api().PATCH("/api/v1/golden/{pair_id}", { params: { path: { pair_id: p.id } }, body: p.body })),
		(_v, r) => r.corpus_id,
	);

export const useDeleteGolden = (kb: string) =>
	useGoldenMutation(
		(id: string) => unwrap(api().DELETE("/api/v1/golden/{pair_id}", { params: { path: { pair_id: id } } })),
		() => kb,
	);

/** `POST /golden/from-feedback`: the feedback's correction becomes the expected answer. */
export const useGoldenFromFeedback = () =>
	useGoldenMutation(
		(feedbackId: string) => unwrap(api().POST("/api/v1/golden/from-feedback", { body: { feedback_id: feedbackId } })),
		(_v, r) => r.corpus_id,
	);

export async function exportGolden(kb: string) {
	return unwrap(api().GET("/api/v1/golden/export", { params: { query: { corpus_id: kb } } }));
}

/**
 * `POST /golden/import?corpus_id=` (multipart `file`). A UTF-8 byte-order mark is removed first:
 * the engine reads the file as plain UTF-8, so a BOM would hide the `query` column and every row
 * would be skipped.
 */
export function useImportGolden(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: async (file: File) => {
			// CSV is text: decoding as UTF-8 and removing U+FEFF is the same as dropping the BOM bytes.
			const text = await file.text();
			const clean = text.charCodeAt(0) === 0xfeff ? new File([text.slice(1)], file.name, { type: "text/csv" }) : file;
			return upload<S["GoldenImportResponse"]>({
				path: `/api/v1/golden/import?corpus_id=${encodeURIComponent(kb)}`,
				buildForm: () => {
					const form = new FormData();
					form.append("file", clean, file.name);
					return form;
				},
			});
		},
		onSuccess: () => qc.invalidateQueries({ queryKey: keys.golden(kb) }),
	});
}

// ---- evaluation runs (admin) ----

export function evalRunsQuery(page: number) {
	return queryOptions({
		queryKey: keys.evalList(page),
		queryFn: async () =>
			(await unwrap(
				api().GET("/api/v1/admin/eval/runs", {
					params: { query: { offset: (page - 1) * RUNS_PAGE_SIZE, limit: RUNS_PAGE_SIZE } },
				}),
			)) as Paged<EvalRunSummary>,
		placeholderData: keepPreviousData,
		refetchInterval: (q) => (q.state.data?.items.some((r) => !isTerminal(r.status)) ? POLL_MS : false),
	});
}

export function evalRunQuery(id: string) {
	return queryOptions({
		queryKey: keys.evalRun(id),
		queryFn: () => unwrap(api().GET("/api/v1/admin/eval/runs/{run_id}", { params: { path: { run_id: id } } })),
		refetchInterval: (q) => (q.state.data && isTerminal(q.state.data.status) ? false : POLL_MS),
	});
}

export function useTriggerEval() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: { corpus_id: string | null; sample_size: number | null }) =>
			unwrap(api().POST("/api/v1/admin/eval/trigger", { body })),
		onSuccess: () => qc.invalidateQueries({ queryKey: keys.evals }),
	});
}

// ---- optimisation runs (admin) ----

export function optimizeRunsQuery(page: number) {
	return queryOptions({
		queryKey: keys.optimizeList(page),
		queryFn: async () =>
			(await unwrap(
				api().GET("/api/v1/admin/optimize/runs", {
					params: { query: { offset: (page - 1) * RUNS_PAGE_SIZE, limit: RUNS_PAGE_SIZE } },
				}),
			)) as Paged<OptimizationRunSummary>,
		placeholderData: keepPreviousData,
		refetchInterval: (q) => (q.state.data?.items.some((r) => !isTerminal(r.status)) ? POLL_MS : false),
	});
}

export function optimizeRunQuery(id: string) {
	return queryOptions({
		queryKey: keys.optimizeRun(id),
		queryFn: () => unwrap(api().GET("/api/v1/admin/optimize/runs/{run_id}", { params: { path: { run_id: id } } })),
		refetchInterval: (q) => (q.state.data && isTerminal(q.state.data.status) ? false : POLL_MS),
	});
}

export function useTriggerOptimize() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: { corpus_id: string | null; n_trials: number | null }) =>
			unwrap(api().POST("/api/v1/admin/optimize/trigger", { body })),
		onSuccess: () => qc.invalidateQueries({ queryKey: keys.optimize }),
	});
}
