import { keepPreviousData, queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

/**
 * Documents of a knowledge base (D4): list/search, detail with chunks, versions, restore, merge,
 * delete, and the pending-review queue. Invalidations follow the spec: restore → detail +
 * versions; delete/merge → list + detail + corpora counts + plan; confirm → jobs.
 */
type S = components["schemas"];
export type DocumentSummary = S["DocumentSummary"];
export type DocumentDetail = S["DocumentDetailResponse"];
export type ChunkView = S["ChunkView"];
export type VersionSummary = S["VersionSummary"];
export type Job = S["JobStatusResponse"];
export type SimilarDocument = S["SimilarDocumentInfo"];

export const DOCUMENTS_PAGE_SIZE = 25;
export const CHUNK_PAGE_SIZE = 50;

export function documentsQuery(kb: string, params: { search?: string; offset?: number; limit?: number }) {
	return queryOptions({
		queryKey: qk.documents.list(kb, params),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/documents", {
					params: { query: { corpus_id: kb, search: params.search || undefined, ...pageOf(params) } },
				}),
			),
		placeholderData: keepPreviousData,
	});
}

const pageOf = (p: { offset?: number; limit?: number }) => ({
	offset: p.offset ?? 0,
	limit: p.limit ?? DOCUMENTS_PAGE_SIZE,
});

export function documentQuery(kb: string, id: string, params: { version?: string; chunkOffset?: number }) {
	return queryOptions({
		queryKey: qk.documents.detail(id, params),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/documents/{document_id}", {
					params: {
						path: { document_id: id },
						query: {
							corpus_id: kb,
							version_id: params.version,
							chunk_offset: params.chunkOffset ?? 0,
							chunk_limit: CHUNK_PAGE_SIZE,
						},
					},
				}),
			),
		placeholderData: (prev) => (prev?.id === id ? prev : undefined),
	});
}

export function versionsQuery(kb: string, id: string) {
	return queryOptions({
		queryKey: qk.documents.versions(id),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/documents/{document_id}/versions", {
					params: { path: { document_id: id }, query: { corpus_id: kb } },
				}),
			),
	});
}

/** Jobs waiting for a "looks like an existing document" decision. */
export function pendingReviewsQuery(kb: string) {
	return queryOptions({
		queryKey: qk.ingest.jobs({ corpus_id: kb, status: "pending_review", limit: 100 }),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/ingest/jobs", {
					params: { query: { corpus_id: kb, status: "pending_review", limit: 100 } },
				}),
			),
	});
}

export const useDocuments = (kb: string, params: { search?: string; offset?: number; limit?: number }) =>
	useQuery(documentsQuery(kb, params));
export const useDocument = (kb: string, id: string, params: { version?: string; chunkOffset?: number }) =>
	useQuery(documentQuery(kb, id, params));
export const useVersions = (kb: string, id: string) => useQuery(versionsQuery(kb, id));
export const usePendingReviews = (kb: string) => useQuery(pendingReviewsQuery(kb));

type QC = ReturnType<typeof useQueryClient>;

/** After anything that changes which documents exist (count, plan usage). */
export function invalidateDocumentSet(qc: QC, kb: string) {
	return Promise.all([
		qc.invalidateQueries({ queryKey: qk.documents.all }),
		qc.invalidateQueries({ queryKey: qk.corpora.detail(kb) }),
		qc.invalidateQueries({ queryKey: ["corpora", "list"] }),
		qc.invalidateQueries({ queryKey: qk.plan.me }),
	]);
}

export function useRestoreVersion(kb: string, id: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (versionId: string) =>
			unwrap(
				api().POST("/api/v1/documents/{document_id}/versions/{version_id}/restore", {
					params: { path: { document_id: id, version_id: versionId }, query: { corpus_id: kb } },
				}),
			),
		onSuccess: () =>
			Promise.all([
				qc.invalidateQueries({ queryKey: qk.documents.detailAll(id) }),
				qc.invalidateQueries({ queryKey: qk.documents.versions(id) }),
				// A restore may bring back a document that had no active version (counts, plan).
				qc.invalidateQueries({ queryKey: ["documents", "list"] }),
				qc.invalidateQueries({ queryKey: qk.plan.me }),
			]),
	});
}

/**
 * `confirmName` is the document's file name (or record id): a session ignores it, an API key must
 * send it (the engine's delete guardrail).
 */
export function useDeleteDocument(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, confirmName }: { id: string; confirmName?: string }) =>
			unwrap(
				api().DELETE("/api/v1/documents/{document_id}", {
					params: { path: { document_id: id }, query: { corpus_id: kb, confirm_name: confirmName } },
				}),
			),
		onSettled: (_d, _e, { id }) => {
			qc.removeQueries({ queryKey: qk.documents.detailAll(id) });
			return invalidateDocumentSet(qc, kb);
		},
	});
}

/** POST /documents/{this}/replace/{other}: this document's versions move under `other`. */
export function useMergeDocument(kb: string, id: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (other: string) =>
			unwrap(
				api().POST("/api/v1/documents/{new_document_id}/replace/{old_document_id}", {
					params: { path: { new_document_id: id, old_document_id: other }, query: { corpus_id: kb } },
				}),
			),
		onSuccess: () => {
			qc.removeQueries({ queryKey: qk.documents.detailAll(id) });
			return invalidateDocumentSet(qc, kb);
		},
	});
}

export function useConfirmReview(kb: string) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (v: { jobId: string; action: "new" | "replace" | "skip"; target?: string }) =>
			unwrap(
				api().POST("/api/v1/ingest/{job_id}/confirm", {
					params: { path: { job_id: v.jobId } },
					body: { action: v.action, target_document_id: v.action === "replace" ? (v.target ?? null) : null },
				}),
			),
		onSettled: (_d, _e, v) =>
			Promise.all([
				qc.invalidateQueries({ queryKey: qk.ingest.all }),
				qc.invalidateQueries({ queryKey: qk.ingest.job(v.jobId) }),
				qc.invalidateQueries({ queryKey: ["documents", "list", kb] }),
			]),
	});
}
