import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

/**
 * The guided-setup API (`/api/v1/corpora/{id}/assistant/setup…`, C8). One session per walk through
 * the wizard; its `status` decides the step, so reloading or sharing the `?sid=` URL resumes it.
 * Generating and applying also refresh the Assistant tab's data (the draft and the live version).
 */
type S = components["schemas"];
export type SetupSession = S["SetupSessionResponse"];
export type SetupProfile = S["SetupProfile"];
export type SetupQuestion = S["SetupQuestion"];
export type SetupGenerateResult = S["SetupGenerateResponse"];
export type SetupProposal = S["SetupProposalResponse"];
export type SetupTestsResult = S["SetupTestsResponse"];
export type SetupGaps = S["SetupGapsResponse"];
export type SetupApplyResult = S["SetupApplyResponse"];

const at = (kb: string, sid: string) => ({ params: { path: { corpus_id: kb, sid } } });

export function setupSessionQuery(kb: string, sid: string) {
	return queryOptions({
		queryKey: qk.setup.session(kb, sid),
		queryFn: () => unwrap(api().GET("/api/v1/corpora/{corpus_id}/assistant/setup/{sid}", at(kb, sid))),
	});
}

export function setupQuestionsQuery(kb: string, sid: string) {
	return queryOptions({
		queryKey: qk.setup.questions(kb, sid),
		queryFn: () => unwrap(api().GET("/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/questions", at(kb, sid))),
	});
}

export const isFinishedRun = (status: string | undefined) => status === "completed" || status === "failed";

export function setupGapsQuery(kb: string, sid: string) {
	return queryOptions({
		queryKey: qk.setup.gaps(kb, sid),
		queryFn: () => unwrap(api().GET("/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/gaps", at(kb, sid))),
		refetchInterval: (q) => (isFinishedRun(q.state.data?.run_status) ? false : 2000),
	});
}

export function useStartSetup(kb: string) {
	return useMutation({
		mutationFn: (body: S["SetupStartRequest"]) =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/assistant/setup", { params: { path: { corpus_id: kb } }, body })),
	});
}

/** Every step that changes the session refreshes it and the questions (their prefill follows it). */
function useRefreshSession(kb: string, sid: string) {
	const qc = useQueryClient();
	return (assistant = false) => {
		void qc.invalidateQueries({ queryKey: qk.setup.session(kb, sid) });
		void qc.invalidateQueries({ queryKey: qk.setup.questions(kb, sid) });
		if (assistant) void qc.invalidateQueries({ queryKey: qk.assistant.all });
	};
}

export function useProposeSetup(kb: string, sid: string) {
	const refresh = useRefreshSession(kb, sid);
	return useMutation({
		mutationFn: ({ body, signal }: { body: S["SetupProposeRequest"]; signal?: AbortSignal }) =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/propose", { ...at(kb, sid), body, signal })),
		onSuccess: () => refresh(),
	});
}

export function useAnswerSetup(kb: string, sid: string) {
	const refresh = useRefreshSession(kb, sid);
	return useMutation({
		mutationFn: (body: S["SetupAnswersRequest"]) =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/answers", { ...at(kb, sid), body })),
		onSuccess: () => refresh(),
	});
}

export function useGenerateSetup(kb: string, sid: string) {
	const refresh = useRefreshSession(kb, sid);
	return useMutation({
		mutationFn: (body: S["SetupGenerateRequest"]) =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/generate", { ...at(kb, sid), body })),
		onSuccess: () => refresh(true),
	});
}

export function useRunSetupTests(kb: string, sid: string) {
	const qc = useQueryClient();
	const refresh = useRefreshSession(kb, sid);
	return useMutation({
		// No body: the route takes none, and the engine refuses a field it does not declare.
		mutationFn: () => unwrap(api().POST("/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/tests", at(kb, sid))),
		onSuccess: () => {
			refresh();
			void qc.invalidateQueries({ queryKey: qk.setup.gaps(kb, sid) });
			void qc.invalidateQueries({ queryKey: qk.evals.all });
		},
	});
}

export function useApplySetup(kb: string, sid: string) {
	const refresh = useRefreshSession(kb, sid);
	return useMutation({
		mutationFn: (body: S["SetupApplyRequest"]) =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/apply", { ...at(kb, sid), body })),
		onSuccess: () => refresh(true),
	});
}
