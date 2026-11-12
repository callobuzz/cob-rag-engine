import { keepPreviousData, queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authFetch } from "@/lib/api/auth-fetch";
import { api, unwrap } from "@/lib/api/client";
import { errorFromBody, toApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { upload } from "@/lib/api/upload";
import type { Principal } from "@/lib/auth/principal";
import { currentRagUrl } from "@/lib/auth/store";

/**
 * Content Portal API (D11): `/api/v1/portal/*`, scoped by A0 (`scoped_workspace`, `?corpus_id=` on
 * create and list, `path_corpus` for rules). Every write invalidates the workspace (its detail and
 * everything under it) and the workspace lists; ingest and finalize also the documents, the
 * knowledge base and the plan.
 */
type S = components["schemas"];
export type Workspace = S["WorkspaceResponse"];
export type WorkspaceList = S["WorkspaceListResponse"];
export type StagedContent = S["StagedContentResponse"];
export type Chunk = S["ChunkResponse"];
export type ChunkList = S["ChunkListResponse"];
export type Rule = S["ContentRuleResponse"];
export type RuleCreate = S["CreateRuleRequest"];
export type RuleUpdate = S["UpdateRuleRequest"];
export type AuditEntry = S["AuditTrailEntry"];
export type ChatMessage = S["ChatHistoryMessage"];
export type ChatReply = S["ChatMessageResponse"];
export type ToolCall = S["ToolCallInfo"];
export type ExecuteResult = S["ExecuteActionResponse"];
export type FinalizeResult = S["FinalizeReviewResponse"];
export type ReviewStatus = S["PostIngestReviewStatus"];
export type ReviewCreated = S["CreatePostIngestReviewResponse"];
export type IngestResult = S["IngestWorkspaceResponse"];

export const WORKSPACES_PAGE_SIZE = 20;
export const CHUNKS_PAGE_SIZE = 200;
export const AUDIT_PAGE_SIZE = 100;
export const WORKSPACE_STATUSES = ["draft", "in_review", "approved", "ingested", "archived"] as const;
export const CHUNK_STATUSES = ["draft", "approved", "rejected"] as const;
export type ChunkStatus = (typeof CHUNK_STATUSES)[number];

/** Query keys: everything of one workspace sits under `pk.workspace(id)`, so one invalidation covers it. */
export const pk = {
	all: ["portal"] as const,
	lists: ["portal", "workspaces"] as const,
	workspaces: (kb: string, params: { status?: string; page?: number }) => ["portal", "workspaces", kb, params] as const,
	workspace: (id: string) => ["portal", "workspace", id] as const,
	detail: (id: string) => ["portal", "workspace", id, "detail"] as const,
	content: (id: string) => ["portal", "workspace", id, "content"] as const,
	chunks: (id: string, params: { status?: string; page?: number }) =>
		["portal", "workspace", id, "chunks", params] as const,
	chat: (id: string) => ["portal", "workspace", id, "chat"] as const,
	audit: (id: string, page: number) => ["portal", "workspace", id, "audit", page] as const,
	reviewStatus: (id: string) => ["portal", "workspace", id, "review-status"] as const,
	rules: (kb: string) => ["portal", "rules", kb] as const,
};

/**
 * Who acts, as the body's `user` (A0 `portal_actor`): a session sends nothing (the engine records
 * the signed-in e-mail); an API key names its principal ("admin key" / the tenant's name).
 */
export function actorFields(p: Principal | null): { user?: string } {
	if (!p || p.auth === "session") return {};
	return { user: actorName(p) };
}

/** The name the engine records for this principal (lock holder, audit trail). */
export function actorName(p: Principal | null): string {
	if (!p) return "";
	if (p.auth === "session") return p.user?.email ?? "";
	return p.keyKind === "admin" ? "admin key" : (p.tenant?.name ?? "tenant key");
}

const TERMINAL_AI = new Set(["completed", "failed"]);
export const isAiDone = (c: Pick<StagedContent, "ai_status">) => TERMINAL_AI.has(c.ai_status);

/** POST a JSON body to an operation whose body the OpenAPI document leaves untyped (content upload). */
async function postJson<T>(path: string, body: unknown): Promise<T> {
	let res: Response;
	try {
		res = await authFetch(`${currentRagUrl()}${path}`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		});
	} catch (e) {
		throw toApiError(e);
	}
	const text = await res.text();
	let data: unknown = null;
	try {
		data = text ? JSON.parse(text) : null;
	} catch {
		data = text;
	}
	if (!res.ok) throw errorFromBody(res.status, data, res.headers);
	return data as T;
}

const ws = (id: string) => ({ path: { workspace_id: id } });

// ---- queries ---------------------------------------------------------------------------------

export function workspacesQuery(kb: string, params: { status?: string; page?: number }) {
	const page = params.page ?? 1;
	return queryOptions({
		queryKey: pk.workspaces(kb, params),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/portal/workspaces", {
					params: {
						query: {
							corpus_id: kb,
							status: params.status || undefined,
							offset: (page - 1) * WORKSPACES_PAGE_SIZE,
							limit: WORKSPACES_PAGE_SIZE,
						},
					},
				}),
			),
		placeholderData: keepPreviousData,
	});
}

export function workspaceQuery(id: string) {
	return queryOptions({
		queryKey: pk.detail(id),
		queryFn: () => unwrap(api().GET("/api/v1/portal/workspaces/{workspace_id}", { params: ws(id) })),
	});
}

export function contentQuery(id: string) {
	return queryOptions({
		queryKey: pk.content(id),
		queryFn: () => unwrap(api().GET("/api/v1/portal/workspaces/{workspace_id}/content", { params: ws(id) })),
		// D11: every 3 s while any item is still being structured.
		refetchInterval: (q) => (q.state.data?.items.some((c) => !isAiDone(c)) ? 3000 : false),
	});
}

export function chunksQuery(id: string, params: { status?: string; page?: number }) {
	const page = params.page ?? 1;
	return queryOptions({
		queryKey: pk.chunks(id, params),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/portal/workspaces/{workspace_id}/chunks", {
					params: {
						...ws(id),
						query: {
							status: params.status || undefined,
							offset: (page - 1) * CHUNKS_PAGE_SIZE,
							limit: CHUNKS_PAGE_SIZE,
						},
					},
				}),
			),
		placeholderData: keepPreviousData,
	});
}

export function chatHistoryQuery(id: string) {
	return queryOptions({
		queryKey: pk.chat(id),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/portal/workspaces/{workspace_id}/chat/history", {
					params: { ...ws(id), query: { limit: 200 } },
				}),
			),
	});
}

export function auditQuery(id: string, page: number) {
	return queryOptions({
		queryKey: pk.audit(id, page),
		queryFn: () =>
			unwrap(
				api().GET("/api/v1/portal/workspaces/{workspace_id}/audit-trail", {
					params: { ...ws(id), query: { offset: (page - 1) * AUDIT_PAGE_SIZE, limit: AUDIT_PAGE_SIZE } },
				}),
			),
		placeholderData: keepPreviousData,
	});
}

export function reviewStatusQuery(id: string) {
	return queryOptions({
		queryKey: pk.reviewStatus(id),
		queryFn: () => unwrap(api().GET("/api/v1/portal/workspaces/{workspace_id}/review-status", { params: ws(id) })),
	});
}

export function rulesQuery(kb: string) {
	return queryOptions({
		queryKey: pk.rules(kb),
		queryFn: () =>
			unwrap(api().GET("/api/v1/portal/corpus/{corpus_id}/rules", { params: { path: { corpus_id: kb } } })),
	});
}

export const useWorkspaces = (kb: string, params: { status?: string; page?: number }) =>
	useQuery(workspacesQuery(kb, params));
export const useWorkspace = (id: string) => useQuery(workspaceQuery(id));
export const useRules = (kb: string) => useQuery(rulesQuery(kb));

// ---- writes ----------------------------------------------------------------------------------

type QC = ReturnType<typeof useQueryClient>;

export function invalidateWorkspace(qc: QC, id: string) {
	return Promise.all([
		qc.invalidateQueries({ queryKey: pk.workspace(id) }),
		qc.invalidateQueries({ queryKey: pk.lists }),
	]);
}

/** After ingest or finalize: the knowledge base gained a document or a version. */
export function invalidateLive(qc: QC, kb: string) {
	return Promise.all([
		qc.invalidateQueries({ queryKey: qk.documents.all }),
		qc.invalidateQueries({ queryKey: qk.corpora.detail(kb) }),
		qc.invalidateQueries({ queryKey: ["corpora", "list"] }),
		qc.invalidateQueries({ queryKey: qk.plan.me }),
		qc.invalidateQueries({ queryKey: qk.ingest.all }),
	]);
}

/** A write to one workspace; invalidates it (and, with `live`, the documents and plan) when settled. */
export function useWorkspaceMutation<TVars, TData>(
	workspaceId: string,
	fn: (vars: TVars) => Promise<TData>,
	opts: { live?: string } = {},
) {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: fn,
		onSettled: () =>
			Promise.all([invalidateWorkspace(qc, workspaceId), opts.live ? invalidateLive(qc, opts.live) : null]),
	});
}

export const portal = {
	create: (kb: string, body: { title: string; workflow_mode: string; assigned_reviewer: string | null }) =>
		unwrap(api().POST("/api/v1/portal/workspaces", { params: { query: { corpus_id: kb } }, body })),
	remove: (id: string) => unwrap(api().DELETE("/api/v1/portal/workspaces/{workspace_id}", { params: ws(id) })),
	lock: (id: string, actor: { user?: string }) =>
		unwrap(api().POST("/api/v1/portal/workspaces/{workspace_id}/lock", { params: ws(id), body: actor })),
	unlock: (id: string, actor: { user?: string }) =>
		unwrap(api().POST("/api/v1/portal/workspaces/{workspace_id}/unlock", { params: ws(id), body: actor })),
	/** Best effort on leaving the page: `keepalive` lets it outlive the page (sendBeacon can't send auth headers). */
	unlockOnLeave: (id: string, actor: { user?: string }) => {
		void authFetch(`${currentRagUrl()}/api/v1/portal/workspaces/${id}/unlock`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(actor),
			keepalive: true,
		}).catch(() => undefined);
	},
	addText: (id: string, body: { source_type: "text_dump" | "url"; content: string; title?: string | null }) =>
		postJson<StagedContent>(`/api/v1/portal/workspaces/${id}/content`, body),
	addFile: (id: string, file: File, onProgress?: (fraction: number) => void) =>
		upload<StagedContent>({
			path: `/api/v1/portal/workspaces/${id}/content`,
			buildForm: () => {
				const form = new FormData();
				form.append("file", file);
				return form;
			},
			onProgress,
		}),
	reprocess: (id: string, contentId: string, body: { force: boolean; model_override: string | null }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/content/{content_id}/reprocess", {
				params: { path: { workspace_id: id, content_id: contentId } },
				body,
			}),
		),
	editChunk: (id: string, chunkId: string, text: string, actor: { user?: string }) =>
		unwrap(
			api().PATCH("/api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}", {
				params: { path: { workspace_id: id, chunk_id: chunkId } },
				body: { text, ...actor },
			}),
		),
	splitChunk: (id: string, chunkId: string, position: number, actor: { user?: string }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}/split", {
				params: { path: { workspace_id: id, chunk_id: chunkId } },
				body: { position, ...actor },
			}),
		),
	mergeChunks: (id: string, chunkIds: string[], separator: string, actor: { user?: string }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/chunks/merge", {
				params: ws(id),
				body: { chunk_ids: chunkIds, separator, ...actor },
			}),
		),
	reorderChunks: (id: string, chunkIds: string[], actor: { user?: string }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/chunks/reorder", {
				params: ws(id),
				body: { chunk_ids: chunkIds, ...actor },
			}),
		),
	addChunk: (
		id: string,
		body: { text: string; chunk_index: number | null; staged_document_id: string | null },
		actor: { user?: string },
	) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/chunks", { params: ws(id), body: { ...body, ...actor } }),
		),
	deleteChunk: (id: string, chunkId: string, actor: { user?: string }) =>
		unwrap(
			api().DELETE("/api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}", {
				params: { path: { workspace_id: id, chunk_id: chunkId }, query: { user: actor.user } },
			}),
		),
	approveChunk: (id: string, chunkId: string, actor: { user?: string }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}/approve", {
				params: { path: { workspace_id: id, chunk_id: chunkId } },
				body: { ...actor },
			}),
		),
	rejectChunk: (id: string, chunkId: string, reason: string | null, actor: { user?: string }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}/reject", {
				params: { path: { workspace_id: id, chunk_id: chunkId } },
				body: { reason, ...actor },
			}),
		),
	submit: (id: string, actor: { user?: string }) =>
		unwrap(api().POST("/api/v1/portal/workspaces/{workspace_id}/submit", { params: ws(id), body: actor })),
	approve: (id: string, comment: string | null, actor: { user?: string }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/approve", {
				params: ws(id),
				body: { comment, ...actor },
			}),
		),
	reject: (id: string, feedback: string, actor: { user?: string }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/reject", {
				params: ws(id),
				body: { feedback, ...actor },
			}),
		),
	ingest: (id: string, actor: { user?: string }) =>
		unwrap(api().POST("/api/v1/portal/workspaces/{workspace_id}/ingest", { params: ws(id), body: actor })),
	finalize: (id: string, actor: { user?: string }) =>
		unwrap(api().POST("/api/v1/portal/workspaces/{workspace_id}/finalize-review", { params: ws(id), body: actor })),
	chat: (id: string, message: string, actor: { user?: string }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/chat", {
				params: ws(id),
				// v1 is non-streaming (AUDIT S10).
				body: { message, stream: false, ...actor },
			}),
		),
	execute: (id: string, action: ToolCall, actor: { user?: string }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/{workspace_id}/chat/execute", {
				params: ws(id),
				body: { action, ...actor },
			}),
		),
	fromIngestion: (documentVersionId: string, title: string | null, actor: { user?: string }) =>
		unwrap(
			api().POST("/api/v1/portal/workspaces/from-ingestion", {
				body: { document_version_id: documentVersionId, title, ...actor },
			}),
		),
	createRule: (kb: string, body: RuleCreate) =>
		unwrap(api().POST("/api/v1/portal/corpus/{corpus_id}/rules", { params: { path: { corpus_id: kb } }, body })),
	updateRule: (kb: string, ruleId: string, body: RuleUpdate) =>
		unwrap(
			api().PATCH("/api/v1/portal/corpus/{corpus_id}/rules/{rule_id}", {
				params: { path: { corpus_id: kb, rule_id: ruleId } },
				body,
			}),
		),
	deleteRule: (kb: string, ruleId: string) =>
		unwrap(
			api().DELETE("/api/v1/portal/corpus/{corpus_id}/rules/{rule_id}", {
				params: { path: { corpus_id: kb, rule_id: ruleId } },
			}),
		),
};

/** Rules writes invalidate the rule list of that knowledge base. */
export function useRuleMutation<TVars, TData>(kb: string, fn: (vars: TVars) => Promise<TData>) {
	const qc = useQueryClient();
	return useMutation({ mutationFn: fn, onSettled: () => qc.invalidateQueries({ queryKey: pk.rules(kb) }) });
}
