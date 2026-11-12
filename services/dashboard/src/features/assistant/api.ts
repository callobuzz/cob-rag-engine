import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { ApiError, isApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { assistantConfigSchema, type FullConfig, type TemplateKey, toServerConfig } from "./schemas";
import type { AssistantState, AssistantVersion, CompileResponse, CompileSample, TemplateDetail } from "./types";

/**
 * The assistant API (`/api/v1/corpora/{id}/assistant…`, `/api/v1/assistant/templates`). Every
 * draft change goes through `saveDraft` (whole config + `base_revision`); a stale revision is a
 * 409 `assistant_draft_conflict`, never a silent overwrite. Invalidations touch only
 * `qk.assistant.*`: the knowledge base detail carries no assistant data.
 */
type S = components["schemas"];

const path = (kb: string) => ({ params: { path: { corpus_id: kb } } });

/** The engine always dumps every field, so parsing fills nothing in; a mismatch is refused (assistant_schema_unsupported). */
export function parseConfig(raw: unknown): FullConfig {
	if (!raw || typeof raw !== "object") {
		throw new ApiError({ status: 422, code: "assistant_schema_unsupported", detail: "Unreadable assistant settings." });
	}
	const parsed = assistantConfigSchema.safeParse(raw);
	if (!parsed.success) {
		throw new ApiError({
			status: 422,
			code: "assistant_schema_unsupported",
			detail: "These assistant settings come from a newer engine.",
		});
	}
	return parsed.data;
}

function version(v: S["AssistantVersionResponse"]): AssistantVersion {
	return { ...v, config: parseConfig(v.config) };
}

function stateOf(s: S["AssistantStateResponse"]): AssistantState {
	return { ...s, published: s.published ? version(s.published) : null, draft: s.draft ? version(s.draft) : null };
}

export function assistantStateQuery(kb: string) {
	return queryOptions({
		queryKey: qk.assistant.state(kb),
		queryFn: async () => stateOf(await unwrap(api().GET("/api/v1/corpora/{corpus_id}/assistant", path(kb)))),
	});
}

export function assistantVersionsQuery(kb: string, p: { offset: number; limit: number }) {
	return queryOptions({
		queryKey: qk.assistant.versions(kb, p),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/assistant/versions", { params: { path: { corpus_id: kb }, query: p } }),
			),
		placeholderData: (prev) => prev,
	});
}

export function assistantVersionQuery(kb: string, v: number) {
	return queryOptions({
		queryKey: qk.assistant.version(kb, v),
		queryFn: async () =>
			version(
				await unwrap(
					api().GET("/api/v1/corpora/{corpus_id}/assistant/versions/{version}", {
						params: { path: { corpus_id: kb, version: v } },
					}),
				),
			),
	});
}

export const templatesQuery = queryOptions({
	queryKey: qk.assistant.templates,
	queryFn: () => unwrap(api().GET("/api/v1/assistant/templates")),
	staleTime: 10 * 60_000,
});

export function templateQuery(key: TemplateKey) {
	return queryOptions({
		queryKey: qk.assistant.template(key),
		queryFn: async (): Promise<TemplateDetail> => {
			const t = await unwrap(api().GET("/api/v1/assistant/templates/{key}", { params: { path: { key } } }));
			return { ...t, config: parseConfig(t.config) };
		},
		staleTime: 10 * 60_000,
	});
}

type AssistantConfigInput = S["AssistantConfig"];

/** The single place a form config becomes the request type (cleaned first). */
function asBody(config: FullConfig): AssistantConfigInput {
	return toServerConfig(config) as unknown as AssistantConfigInput;
}

export async function saveDraft(
	kb: string,
	body: { config: FullConfig; base_revision: number },
): Promise<AssistantVersion> {
	return version(
		await unwrap(
			api().PUT("/api/v1/corpora/{corpus_id}/assistant/draft", {
				...path(kb),
				body: { config: asBody(body.config), base_revision: body.base_revision },
			}),
		),
	);
}

export const DEFAULT_SAMPLE: CompileSample = {
	label: "business_question",
	route: "grounded",
	language: "en",
	office_hours: "unknown",
	lead_captured: false,
};

/** The prompt (and its budgets) for a config; nothing is saved and no model is called. */
export function compileConfig(
	kb: string,
	config: FullConfig,
	sample: Partial<CompileSample> = {},
	signal?: AbortSignal,
): Promise<CompileResponse> {
	return unwrap(
		api().POST("/api/v1/corpora/{corpus_id}/assistant/compile", {
			...path(kb),
			body: { config: asBody(config), sample: { ...DEFAULT_SAMPLE, ...sample } },
			signal,
		}),
	);
}

function useAfterChange(kb: string) {
	const qc = useQueryClient();
	return {
		/** A draft was written: show it at once, then confirm with the engine. */
		draft: (out: AssistantVersion) => {
			qc.setQueryData<AssistantState>(qk.assistant.state(kb), (prev) => (prev ? { ...prev, draft: out } : prev));
			void qc.invalidateQueries({ queryKey: qk.assistant.state(kb) });
		},
		/** The live version changed (publish, restore, switch). */
		live: () => {
			void qc.invalidateQueries({ queryKey: qk.assistant.state(kb) });
			void qc.invalidateQueries({ queryKey: ["assistant", "versions", kb] });
		},
	};
}

export function useSaveDraft(kb: string) {
	const after = useAfterChange(kb);
	return useMutation({
		mutationFn: (b: { config: FullConfig; base_revision: number }) => saveDraft(kb, b),
		onSuccess: after.draft,
	});
}

export function useDraftFromTemplate(kb: string) {
	const after = useAfterChange(kb);
	return useMutation({
		mutationFn: async (b: { template: TemplateKey; keep_identity: boolean; base_revision: number }) =>
			version(
				await unwrap(api().POST("/api/v1/corpora/{corpus_id}/assistant/draft/from-template", { ...path(kb), body: b })),
			),
		onSuccess: after.draft,
	});
}

/** A draft that is already gone counts as discarded. */
export function useDiscardDraft(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: async () => {
			try {
				await unwrap(api().DELETE("/api/v1/corpora/{corpus_id}/assistant/draft", path(kb)));
			} catch (e) {
				if (!(isApiError(e) && e.code === "assistant_draft_missing")) throw e;
			}
		},
		onSettled: () => void qc.invalidateQueries({ queryKey: qk.assistant.state(kb) }),
	});
}

export function usePublish(kb: string) {
	const after = useAfterChange(kb);
	return useMutation({
		mutationFn: async (b: { note?: string; base_revision: number }) =>
			version(await unwrap(api().POST("/api/v1/corpora/{corpus_id}/assistant/publish", { ...path(kb), body: b }))),
		onSuccess: after.live,
	});
}

export type ImproveRuleResponse = S["ImproveRuleResponse"];

/** "Improve this rule": one model call that returns a suggestion; nothing is saved. */
export function useImproveRule(kb: string) {
	return useMutation({
		mutationFn: (body: S["ImproveRuleRequest"]) =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/assistant/rules/improve", { ...path(kb), body })),
	});
}

export function useRestoreVersion(kb: string) {
	const after = useAfterChange(kb);
	return useMutation({
		mutationFn: async (v: number) =>
			version(
				await unwrap(
					api().POST("/api/v1/corpora/{corpus_id}/assistant/versions/{version}/restore", {
						params: { path: { corpus_id: kb, version: v } },
					}),
				),
			),
		onSuccess: after.live,
	});
}

/**
 * Switch the assistant on or off. An unpublished draft keeps its own copy of the switch and
 * publishing it must not flip the live one back, so the draft is re-saved with the new value.
 */
export function useSetEnabled(kb: string) {
	const qc = useQueryClient();
	const after = useAfterChange(kb);
	return useMutation({
		mutationFn: async (enabled: boolean) => {
			const out = version(
				await unwrap(api().POST("/api/v1/corpora/{corpus_id}/assistant/enabled", { ...path(kb), body: { enabled } })),
			);
			const draft = qc.getQueryData<AssistantState>(qk.assistant.state(kb))?.draft;
			if (draft && draft.config.enabled !== enabled) {
				try {
					await saveDraft(kb, { config: { ...draft.config, enabled }, base_revision: draft.draft_revision });
				} catch {
					// The engine's own publish keeps the live switch; the next refetch shows the draft as it is.
				}
			}
			return out;
		},
		onSuccess: after.live,
	});
}
