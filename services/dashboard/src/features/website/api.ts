import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type WebsiteOut = S["WebsiteOut"];
export type WebsiteSource = S["SourceOut"];
export type WebsitePage = S["PageOut"];
export type WebsitePages = S["PagesOut"];
export type RefreshMode = NonNullable<S["UpdateWebsiteRequest"]["refresh_mode"]>; // BIZ2
export type LearnKeyState = S["LearnKeyStateOut"]; // BIZ2
export type LearnKeyOut = S["LearnKeyOut"]; // BIZ2

export const PAGE_SIZE = 25;
/** How often the tab looks again while a website is being read. */
export const POLL_MS = 5_000;

export const websiteKey = (kb: string) => ["website", kb] as const;

/** A website is still being read (the tab polls only then). */
export const isBusy = (s: Pick<WebsiteSource, "status">) => s.status === "discovering" || s.status === "learning";

/** `GET /corpora/{id}/website`: settings, the plan's limits, the sites and how far each is. */
export function websiteQuery(kb: string) {
	return queryOptions({
		queryKey: websiteKey(kb),
		queryFn: () => unwrap(api().GET("/api/v1/corpora/{corpus_id}/website", { params: { path: { corpus_id: kb } } })),
		refetchInterval: (query) => (query.state.data?.sources.some(isBusy) ? POLL_MS : false),
	});
}

export interface PagesParams {
	status?: string;
	q?: string;
	page: number;
}

/** `GET …/website/pages`, filtered and paged. Looks again every few seconds while `poll`. */
export function websitePagesQuery(kb: string, p: PagesParams, poll = false) {
	return queryOptions({
		queryKey: [...websiteKey(kb), "pages", p] as const,
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/website/pages", {
					params: {
						path: { corpus_id: kb },
						query: {
							status: p.status || undefined,
							q: p.q || undefined,
							offset: (p.page - 1) * PAGE_SIZE,
							limit: PAGE_SIZE,
						},
					},
				}),
			),
		placeholderData: (prev) => prev,
		refetchInterval: poll ? POLL_MS : false,
	});
}

function useRefresh(kb: string) {
	const qc = useQueryClient();
	return () => qc.invalidateQueries({ queryKey: websiteKey(kb) });
}

/** Start reading one of the allowed sites. */
export function useLearnWebsite(kb: string) {
	const refresh = useRefresh(kb);
	return useMutation({
		mutationFn: (origin: string) =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/website/learn", {
					params: { path: { corpus_id: kb } },
					body: { origin },
				}),
			),
		onSuccess: refresh,
	});
}

export function useUpdateWebsite(kb: string) {
	const refresh = useRefresh(kb);
	return useMutation({
		mutationFn: (body: S["UpdateWebsiteRequest"]) =>
			unwrap(api().PATCH("/api/v1/corpora/{corpus_id}/website", { params: { path: { corpus_id: kb } }, body })),
		onSuccess: refresh,
	});
}

export function useRereadWebsite(kb: string) {
	const refresh = useRefresh(kb);
	return useMutation({
		mutationFn: () =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/website/reread", { params: { path: { corpus_id: kb } } })),
		onSuccess: refresh,
	});
}

export function useRemoveSource(kb: string) {
	const refresh = useRefresh(kb);
	return useMutation({
		mutationFn: (sourceId: string) =>
			unwrap(
				api().DELETE("/api/v1/corpora/{corpus_id}/website/sources/{source_id}", {
					params: { path: { corpus_id: kb, source_id: sourceId } },
				}),
			),
		onSuccess: refresh,
	});
}

export function useRereadPage(kb: string) {
	const refresh = useRefresh(kb);
	return useMutation({
		mutationFn: (pageId: string) =>
			unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/website/pages/{page_id}/reread", {
					params: { path: { corpus_id: kb, page_id: pageId } },
				}),
			),
		onSuccess: refresh,
	});
}

/** Leave a page out (archives it now) or include it again (queues it). The row updates at once. */
export function useExcludePage(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ pageId, excluded }: { pageId: string; excluded: boolean }) =>
			unwrap(
				api().PATCH("/api/v1/corpora/{corpus_id}/website/pages/{page_id}", {
					params: { path: { corpus_id: kb, page_id: pageId } },
					body: { excluded },
				}),
			),
		onMutate: async ({ pageId, excluded }) => {
			await qc.cancelQueries({ queryKey: [...websiteKey(kb), "pages"] });
			const before = qc.getQueriesData<WebsitePages>({ queryKey: [...websiteKey(kb), "pages"] });
			for (const [key, data] of before) {
				if (!data) continue;
				qc.setQueryData<WebsitePages>(key, {
					...data,
					items: data.items.map((p) =>
						p.id === pageId
							? { ...p, status: excluded ? "excluded" : "queued", excluded_by_owner: excluded, skip_reason: null }
							: p,
					),
				});
			}
			return { before };
		},
		onError: (_e, _v, ctx) => {
			for (const [key, data] of ctx?.before ?? []) qc.setQueryData(key, data);
		},
		onSettled: () => qc.invalidateQueries({ queryKey: websiteKey(kb) }),
	});
}

// BIZ2
/** Make (or replace) the push link. The key comes back once, in this response only. */
export function useCreateLearnKey(kb: string) {
	const refresh = useRefresh(kb);
	return useMutation({
		mutationFn: () =>
			unwrap(api().POST("/api/v1/corpora/{corpus_id}/website/learn-key", { params: { path: { corpus_id: kb } } })),
		onSuccess: refresh,
	});
}

export function useDeleteLearnKey(kb: string) {
	const refresh = useRefresh(kb);
	return useMutation({
		mutationFn: () =>
			unwrap(api().DELETE("/api/v1/corpora/{corpus_id}/website/learn-key", { params: { path: { corpus_id: kb } } })),
		onSuccess: refresh,
	});
}
// /BIZ2
