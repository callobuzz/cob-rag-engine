import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { upload } from "@/lib/api/upload";

/** COB9: the documents COB reads (`GET /admin/cob/documents`) and the platform-doc writes. */
export type CobDocument = components["schemas"]["CobDocumentOut"];
export type CobAudience = CobDocument["audience"];
export const AUDIENCES: CobAudience[] = ["customer", "public", "admin"];

export const cobDocsKey = ["admin", "cob", "documents"] as const;

export const cobDocsQuery = queryOptions({
	queryKey: cobDocsKey,
	queryFn: () => unwrap(api().GET("/api/v1/admin/cob/documents", { params: { query: { source: "all" } } })),
});

function useRefresh() {
	const qc = useQueryClient();
	return () => qc.invalidateQueries({ queryKey: cobDocsKey });
}

/** POST /ingest into COB's knowledge base: a file, with the audience as its metadata. */
export function useUploadPlatformDoc(corpusId: string | null | undefined) {
	const refresh = useRefresh();
	return useMutation({
		mutationFn: ({ file, audience }: { file: File; audience: CobAudience }) =>
			upload<{ job_id: string }>({
				path: "/api/v1/ingest",
				buildForm: () => {
					const f = new FormData();
					f.append("corpus_id", corpusId ?? "");
					f.append("metadata", JSON.stringify({ audience }));
					f.append("auto_confirm", "true");
					f.append("file", file, file.name);
					return f;
				},
			}),
		onSettled: refresh,
	});
}

/** POST /ingest/records: a typed page (the id is the title, so adding it again replaces it). */
export function useAddPlatformNote(corpusId: string | null | undefined) {
	const refresh = useRefresh();
	return useMutation({
		mutationFn: ({ title, text, audience }: { title: string; text: string; audience: CobAudience }) =>
			unwrap(
				api().POST("/api/v1/ingest/records", {
					body: {
						corpus_id: corpusId ?? undefined,
						records: [{ id: `note:${title.trim()}`, title: title.trim(), text, metadata: { audience } }],
					},
				}),
			),
		onSettled: refresh,
	});
}

export function useSetDocAudience() {
	const refresh = useRefresh();
	return useMutation({
		mutationFn: ({ id, audience }: { id: string; audience: CobAudience }) =>
			unwrap(
				api().PATCH("/api/v1/admin/cob/documents/{document_id}", {
					params: { path: { document_id: id } },
					body: { audience },
				}),
			),
		onSettled: refresh,
	});
}

export function useDeletePlatformDoc(corpusId: string | null | undefined) {
	const refresh = useRefresh();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(
				api().DELETE("/api/v1/documents/{document_id}", {
					params: { path: { document_id: id }, query: { corpus_id: corpusId ?? "" } },
				}),
			),
		onSettled: refresh,
	});
}
