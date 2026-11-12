import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/** The legal pages (P3): the operator's editor, and the public list sign-up reads. */
type S = components["schemas"];
export type LegalPageSummary = S["LegalPageAdmin"];
export type LegalPage = S["LegalPageDetail"];
export type LegalSettings = S["LegalSettings"];
export type LegalAcceptances = S["LegalAcceptancesOut"];
export type PublicLegalItem = S["PublicLegalItem"];

export const legalKeys = {
	all: ["admin", "legal"] as const,
	list: ["admin", "legal", "list"] as const,
	page: (slug: string) => ["admin", "legal", "page", slug] as const,
	settings: ["admin", "legal", "settings"] as const,
	acceptances: (offset: number) => ["admin", "legal", "acceptances", offset] as const,
	public: ["public", "legal"] as const,
};

export const ACCEPTANCES_PAGE = 50;

export const legalListQuery = queryOptions({
	queryKey: legalKeys.list,
	queryFn: async () => (await unwrap(api().GET("/api/v1/admin/legal"))).items,
});

export const legalPageQuery = (slug: string) =>
	queryOptions({
		queryKey: legalKeys.page(slug),
		queryFn: () => unwrap(api().GET("/api/v1/admin/legal/{slug}", { params: { path: { slug } } })),
	});

export const legalSettingsQuery = queryOptions({
	queryKey: legalKeys.settings,
	queryFn: () => unwrap(api().GET("/api/v1/admin/legal/settings")),
});

export const acceptancesQuery = (offset: number) =>
	queryOptions({
		queryKey: legalKeys.acceptances(offset),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/admin/legal/acceptances", {
					params: { query: { offset, limit: ACCEPTANCES_PAGE } },
				}),
			) as Promise<LegalAcceptances>,
	});

/** Every accepted version as the CSV the engine writes (fetched with the session's credentials). */
export async function loadAcceptancesCsv(): Promise<string> {
	return (await unwrap(
		api().GET("/api/v1/admin/legal/acceptances", { params: { query: { format: "csv" } }, parseAs: "text" }),
	)) as unknown as string;
}

/** The pages published for visitors, with versions: what the sign-up form asks to accept. */
export const publicLegalQuery = queryOptions({
	queryKey: legalKeys.public,
	queryFn: async () => (await unwrap(api().GET("/api/v1/public/legal"))).items,
	staleTime: 30_000,
});

export function useSaveDraft(slug: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: { title: string; markdown: string; base_revision: number }) =>
			unwrap(api().PUT("/api/v1/admin/legal/{slug}/draft", { params: { path: { slug } }, body })),
		onSuccess: (data) => {
			qc.setQueryData(legalKeys.page(slug), data);
			return qc.invalidateQueries({ queryKey: legalKeys.list });
		},
	});
}

export function usePreview(slug: string) {
	return useMutation({
		mutationFn: (markdown: string) =>
			unwrap(api().POST("/api/v1/admin/legal/{slug}/preview", { params: { path: { slug } }, body: { markdown } })),
	});
}

export function usePublish(slug: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: { change_note: string | null; confirm_reviewed: boolean }) =>
			unwrap(api().POST("/api/v1/admin/legal/{slug}/publish", { params: { path: { slug } }, body })),
		onSuccess: (data) => {
			qc.setQueryData(legalKeys.page(slug), data);
			void qc.invalidateQueries({ queryKey: legalKeys.list });
			// the footers and the sign-up form follow the published pages
			void qc.invalidateQueries({ queryKey: legalKeys.public });
			return qc.invalidateQueries({ queryKey: ["public", "branding"] });
		},
	});
}

export function useSaveLegalSettings() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: LegalSettings) => unwrap(api().PUT("/api/v1/admin/legal/settings", { body })),
		onSuccess: (data) => {
			qc.setQueryData(legalKeys.settings, data);
			return qc.invalidateQueries({ queryKey: ["public", "branding"] });
		},
	});
}

/** The address of a published page on the engine (it serves plain HTML, outside `/api`). */
export function enginePageUrl(ragUrl: string, slug: string): string {
	return `${ragUrl.replace(/\/$/, "")}/legal/${slug}`;
}
