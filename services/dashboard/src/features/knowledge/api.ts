import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { invalidateDocumentSet } from "@/features/documents/api";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

export type Article = components["schemas"]["ArticleOut"];
export type ArticleKind = "article" | "faq";
export type KnowledgeDoc = components["schemas"]["DocumentSummary"];
export type KnowledgeSource = "articles" | "files";

export const KNOWLEDGE_PAGE_SIZE = 100;

/**
 * What the owner wrote (`source=articles`) or uploaded (`source=files`) in one knowledge base.
 * The key sits under `qk.documents.all`, so every document change (delete, upload, import) also
 * refreshes these lists.
 */
export function knowledgeDocsQuery(kb: string, source: KnowledgeSource, poll = false) {
	return queryOptions({
		queryKey: [...qk.documents.all, "knowledge", kb, source] as const,
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/documents", {
					params: { query: { corpus_id: kb, source, limit: KNOWLEDGE_PAGE_SIZE, offset: 0 } },
				}),
			),
		// A new article takes a moment to be learned; look again every few seconds until it shows.
		refetchInterval: poll ? 3_000 : false,
	});
}

/** `GET /corpora/{id}/articles/{document or article id}`: the text exactly as written. */
export function articleQuery(kb: string, id: string) {
	return queryOptions({
		queryKey: qk.articles.detail(kb, id),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/corpora/{corpus_id}/articles/{document_id}", {
					params: { path: { corpus_id: kb, document_id: id } },
				}),
			),
		staleTime: 0,
	});
}

export interface ArticleInput {
	kind: ArticleKind;
	title: string;
	body: string;
}

/** Create (no `id`) or change (`id`) an article or a question and answer. The engine answers 202. */
export function useSaveArticle(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, input }: { id?: string; input: ArticleInput }) =>
			id
				? unwrap(
						api().PUT("/api/v1/corpora/{corpus_id}/articles/{document_id}", {
							params: { path: { corpus_id: kb, document_id: id } },
							body: input,
						}),
					)
				: unwrap(
						api().POST("/api/v1/corpora/{corpus_id}/articles", {
							params: { path: { corpus_id: kb } },
							body: input,
						}),
					),
		onSettled: async (_data, _error, { id }) => {
			if (id) await qc.invalidateQueries({ queryKey: qk.articles.all });
			await invalidateDocumentSet(qc, kb);
		},
	});
}

/** Which kind of thing a document is, from how it was added. */
export function docKind(d: Pick<KnowledgeDoc, "external_id">): ArticleKind | "file" {
	const id = d.external_id ?? "";
	if (id.startsWith("article:")) return "article";
	if (id.startsWith("faq:")) return "faq";
	return "file";
}
