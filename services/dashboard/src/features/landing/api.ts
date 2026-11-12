import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { corporaQuery, operatorWorkspaceQuery } from "@/features/kbs/api";
import { assignablePlansQuery } from "@/features/plans/api";
import { authFetch } from "@/lib/api/auth-fetch";
import { api, unwrap } from "@/lib/api/client";
import { errorFromBody, toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { upload } from "@/lib/api/upload";
import { currentRagUrl } from "@/lib/auth/store";

/** The public website's editor (LP4): the operator's draft, publishing, versions, uploads and the "Ask it yourself" link. */
type S = components["schemas"];
export type LandingState = S["LandingState"];
export type LandingContent = S["LandingContent"];
export type LandingAsset = S["LandingAssetOut"];
export type LandingThemePreview = S["LandingThemePreview"];
export type LandingVersion = S["LandingVersionOut"];
export type LandingLang = LandingContent["default_language"];
export type PageKey = "home" | "pricing" | "features" | "about" | "contact";
export type AskStatus = LandingState["ask"]["status"];

export const landingKeys = {
	all: ["admin", "landing"] as const,
	state: ["admin", "landing", "state"] as const,
	versions: ["admin", "landing", "versions"] as const,
	presets: ["admin", "landing", "presets"] as const,
};

export const landingQuery = queryOptions({
	queryKey: landingKeys.state,
	queryFn: () => unwrap(api().GET("/api/v1/admin/landing")),
});

export const versionsQuery = queryOptions({
	queryKey: landingKeys.versions,
	queryFn: async () => (await unwrap(api().GET("/api/v1/admin/landing/versions"))).items,
});

export const useLanding = () => useQuery(landingQuery);
export const useVersions = () => useQuery(versionsQuery);

/** The server answers every change with the whole state; keep the cache equal to it. */
function useStateWriter() {
	const qc = useQueryClient();
	return (state: LandingState) => {
		qc.setQueryData(landingKeys.state, state);
		return qc.invalidateQueries({ queryKey: landingKeys.versions });
	};
}

/** The public pages follow the website switch (sign-in pages link back to it). */
function useBrandingRefresh() {
	const qc = useQueryClient();
	return () => qc.invalidateQueries({ queryKey: ["public", "branding"] });
}

export function useSaveDraft() {
	const write = useStateWriter();
	return useMutation({
		mutationFn: (body: { base_revision: number; content: LandingContent }) =>
			unwrap(api().PUT("/api/v1/admin/landing/draft", { body })),
		onSuccess: write,
	});
}

export function usePublish() {
	const write = useStateWriter();
	const branding = useBrandingRefresh();
	return useMutation({
		mutationFn: (body: { base_revision: number; change_note?: string | null }) =>
			unwrap(api().POST("/api/v1/admin/landing/publish", { body })),
		onSuccess: async (data) => {
			await write(data);
			await branding();
		},
	});
}

export function useLandingSettings() {
	const write = useStateWriter();
	const branding = useBrandingRefresh();
	return useMutation({
		mutationFn: (body: { enabled?: boolean; address?: string | null }) =>
			unwrap(api().PUT("/api/v1/admin/landing/settings", { body })),
		onSuccess: async (data) => {
			await write(data);
			await branding();
		},
	});
}

export function useThemePreview() {
	return useMutation({
		mutationFn: (body: { brand_color: string; accent_color?: string | null; base: "light" | "dark" | "auto" }) =>
			unwrap(api().POST("/api/v1/admin/landing/theme/preview", { body })),
	});
}

export function usePreviewLink() {
	return useMutation({
		mutationFn: (body: { page: PageKey; lang: LandingLang }) =>
			unwrap(api().POST("/api/v1/admin/landing/preview-link", { body })),
	});
}

export function useRestore() {
	const write = useStateWriter();
	return useMutation({
		mutationFn: (v: { version: number; base_revision: number }) =>
			unwrap(
				api().POST("/api/v1/admin/landing/versions/{version}/restore", {
					params: { path: { version: v.version } },
					body: { base_revision: v.base_revision },
				}),
			),
		onSuccess: write,
	});
}

export function useUploadAsset() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (v: { kind: "logo" | "poster" | "video"; file: File; onProgress?: (fraction: number) => void }) =>
			upload<LandingAsset>({
				path: "/api/v1/admin/landing/assets",
				buildForm: () => {
					const form = new FormData();
					form.append("kind", v.kind);
					form.append("file", v.file);
					return form;
				},
				onProgress: v.onProgress,
			}),
		onSuccess: () => qc.invalidateQueries({ queryKey: landingKeys.state }),
	});
}

export function useDeleteAsset() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(api().DELETE("/api/v1/admin/landing/assets/{asset_id}", { params: { path: { asset_id: id } } })),
		onSuccess: () => qc.invalidateQueries({ queryKey: landingKeys.state }),
	});
}

export function useConnectAsk() {
	const write = useStateWriter();
	return useMutation({
		mutationFn: (corpusId: string) =>
			unwrap(api().POST("/api/v1/admin/landing/assistant", { body: { corpus_id: corpusId } })),
		onSuccess: write,
	});
}

export function useDisconnectAsk() {
	const write = useStateWriter();
	return useMutation({
		mutationFn: () => unwrap(api().DELETE("/api/v1/admin/landing/assistant")),
		onSuccess: write,
	});
}

/** The operator's own knowledge bases: the only ones the website may read from or answer with. */
export function useOperatorKbs() {
	const workspace = useQuery(operatorWorkspaceQuery);
	const tenantId = workspace.data?.id;
	const list = useQuery({ ...corporaQuery({ tenant_id: tenantId, limit: 100 }), enabled: tenantId != null });
	return {
		isPending: workspace.isPending || (tenantId != null && list.isPending),
		hasWorkspace: workspace.isSuccess && workspace.data != null,
		kbs: list.data?.items ?? [],
	};
}

// ----------------------------------------------------------------------------------------------
// Pinned by LP2 (presets, generate): these routes are not in schema.d.ts until LP2 lands, so the
// shapes are written here exactly as the epic README pins them.

export interface PresetSummary {
	key: string;
	name: string;
	languages: LandingLang[];
}
export type ScriptedConversation = components["schemas"]["ScriptedConversation"];
export interface PresetDetail {
	conversation: ScriptedConversation;
	sections: Record<string, unknown>;
}
export interface GenerateResult {
	conversation: ScriptedConversation;
	sources: { title: string; heading: string | null }[];
	cost_note: string;
}

async function pinned<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
	let response: Response;
	try {
		response = await authFetch(
			new Request(`${currentRagUrl()}${path}`, {
				method,
				headers: body === undefined ? {} : { "Content-Type": "application/json" },
				body: body === undefined ? undefined : JSON.stringify(body),
			}),
		);
	} catch (e) {
		throw toApiError(e);
	}
	let parsed: unknown = null;
	try {
		parsed = await response.json();
	} catch {
		parsed = null;
	}
	if (!response.ok) throw errorFromBody(response.status, parsed, response.headers);
	return parsed as T;
}

export const presetsQuery = queryOptions({
	queryKey: landingKeys.presets,
	queryFn: () => pinned<PresetSummary[]>("GET", "/api/v1/admin/landing/presets"),
	staleTime: 10 * 60_000,
});

export const usePresets = () => useQuery(presetsQuery);

export function usePreset() {
	return useMutation({
		mutationFn: (v: { key: string; lang: LandingLang }) =>
			pinned<PresetDetail>("GET", `/api/v1/admin/landing/presets/${encodeURIComponent(v.key)}?lang=${v.lang}`),
	});
}

export function useGenerate() {
	return useMutation({
		mutationFn: (v: { corpus_id: string; language: LandingLang; preset?: string | null }) =>
			pinned<GenerateResult>("POST", "/api/v1/admin/landing/generate", v),
	});
}

/** Plans the pricing cards and the home page's price preview can point at. */
export function usePlanChoices() {
	return useQuery({
		...assignablePlansQuery,
		select: (r) => r.items.map((p) => ({ id: p.id, name: p.name, description: p.description })),
	});
}
