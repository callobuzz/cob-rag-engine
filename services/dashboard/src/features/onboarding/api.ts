import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { upload } from "@/lib/api/upload";

type S = components["schemas"];
export type Onboarding = S["OnboardingOut"];
export type StartOnboarding = S["StartOnboardingRequest"];
export type Suggestion = S["SuggestionOut"];
export type BusinessType = NonNullable<StartOnboarding["business_type"]>;

/** How often the install light and the learning count look again while the wizard is open. */
export const POLL_MS = 5_000;
/** After this long the light stops polling and says what to check. */
export const POLL_GIVE_UP_MS = 15 * 60_000;

/** `GET /api/v1/onboarding`: the step, the code to install, the light and the learning count. */
export function onboardingQuery(poll = false) {
	return queryOptions({
		queryKey: qk.onboarding,
		queryFn: () => unwrap(api().GET("/api/v1/onboarding")),
		refetchInterval: poll ? POLL_MS : false,
		staleTime: 10_000,
	});
}

/** `GET …/website/suggestions`: up to six first questions, from the pages the assistant learned. */
export function suggestionsQuery(kb: string, lang: "en" | "hi-Latn") {
	return queryOptions({
		queryKey: qk.suggestions(kb, lang),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/website/suggestions", {
					params: { path: { corpus_id: kb }, query: { lang } },
				}),
			),
		staleTime: 60_000,
	});
}

/** Everything the wizard changes shows up on Home, the knowledge base list and the widget page. */
function useRefreshAfter() {
	const qc = useQueryClient();
	return (out?: Onboarding) => {
		if (out) qc.setQueryData(qk.onboarding, out);
		void qc.invalidateQueries({ queryKey: qk.onboarding });
		void qc.invalidateQueries({ queryKey: ["home"] });
		void qc.invalidateQueries({ queryKey: qk.corpora.all });
		void qc.invalidateQueries({ queryKey: qk.widgetKeys.all });
		void qc.invalidateQueries({ queryKey: qk.assistant.all });
	};
}

/** `POST /onboarding/website`: safe to repeat; a failed step keeps what succeeded. */
export function useStartOnboarding() {
	const refresh = useRefreshAfter();
	return useMutation({
		mutationFn: (body: StartOnboarding) => unwrap(api().POST("/api/v1/onboarding/website", { body })),
		onSuccess: refresh,
	});
}

/** `PATCH /onboarding`: move to a step, skip the steps (`dismissed`) or finish them (`done`). */
export function useUpdateOnboarding() {
	const refresh = useRefreshAfter();
	return useMutation({
		mutationFn: (body: S["UpdateOnboardingRequest"]) => unwrap(api().PATCH("/api/v1/onboarding", { body })),
		onSuccess: refresh,
	});
}

/** `POST …/assistant/avatar`: the engine re-saves it as a 256 pixel PNG and returns its own path. */
export async function uploadAvatar(kb: string, file: File): Promise<string> {
	const out = await upload<S["AvatarOut"]>({
		path: `/api/v1/corpora/${kb}/assistant/avatar`,
		buildForm: () => {
			const form = new FormData();
			form.append("file", file);
			return form;
		},
	});
	return out.avatar_url;
}

/** Put an uploaded picture in the assistant's draft (the owner publishes it from the Assistant page). */
export function usePatchAvatar(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: async (file: File) => {
			const avatarUrl = await uploadAvatar(kb, file);
			const state = await unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/assistant", { params: { path: { corpus_id: kb } } }),
			);
			await unwrap(
				api().PATCH("/api/v1/corpora/{corpus_id}/assistant/draft", {
					params: { path: { corpus_id: kb } },
					body: {
						patch: { identity: { avatar_url: avatarUrl } },
						base_revision: state.draft?.draft_revision ?? 0,
					},
				}),
			);
			return avatarUrl;
		},
		onSuccess: () => void qc.invalidateQueries({ queryKey: qk.assistant.all }),
	});
}
