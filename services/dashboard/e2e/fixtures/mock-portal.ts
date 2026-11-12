import { randomUUID } from "node:crypto";
import type { BrowserContext, Route } from "@playwright/test";
import type { DocRecord } from "./mock-documents";
import { MOCK_ENGINE, type World } from "./mock-engine";

/**
 * Content Portal part of the fake engine (D11), installed by the spec on top of
 * `installMockEngine` (a later `context.route` runs first; anything else falls back to the engine),
 * so the shared mock-engine index stays untouched. It follows content_portal/routes/*.py and the
 * services: `?corpus_id=` scoping on create and list, the edit lock (423 on lock; the chunk
 * editor's uncoded 409 without it), staged content structured by "the AI" over time, the review
 * workflow, ingest (A6 cap, a new document in the documents mock), post-ingest review with
 * `review_exists` / `version_not_active`, finalize (a new active version) and routing rules.
 * Workspace delete is owner-only (A5). Indian demo data only.
 */

const CORS = {
	"access-control-allow-origin": "*",
	"access-control-allow-headers": "authorization, x-api-key, content-type",
	"access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
};

/** How long "the AI" takes to structure a staged item. */
export const AI_MS = 1500;

interface Ws {
	id: string;
	corpus_id: string;
	title: string;
	kind: "standard" | "post_ingest_review";
	reviewed_version_id: string | null;
	finalized_version_id: string | null;
	status: string;
	workflow_mode: string;
	created_by: string;
	assigned_reviewer: string | null;
	locked_by: string | null;
	locked_at: string | null;
	review_feedback: string | null;
	created_at: string;
	updated_at: string;
	documentId: string | null;
}
interface Staged {
	id: string;
	workspace_id: string;
	source_type: string;
	original_filename: string;
	mime_type: string;
	text: string;
	createdAt: number;
	applied: boolean;
}
interface SChunk {
	id: string;
	workspace_id: string;
	staged_document_id: string | null;
	text: string;
	chunk_index: number;
	metadata_: Record<string, unknown> | null;
	confidence_score: number | null;
	warnings: Record<string, unknown> | null;
	status: string;
	source: string;
	edit_history: Record<string, unknown>[];
	created_at: string;
	updated_at: string;
	original?: string;
}
interface Rule {
	id: string;
	corpus_id: string;
	rule_type: string;
	condition: Record<string, unknown>;
	action: string;
	priority: number;
	enabled: boolean;
	created_at: string;
}

export interface PortalState {
	workspaces: Ws[];
	staged: Staged[];
	chunks: SChunk[];
	rules: Rule[];
	chat: Record<string, Record<string, unknown>[]>;
	/** Request bodies the specs assert on (no `user` from sessions). */
	bodies: { path: string; body: Record<string, unknown> }[];
}

export function createPortalState(): PortalState {
	return { workspaces: [], staged: [], chunks: [], rules: [], chat: {}, bodies: [] };
}

type Who = { admin: boolean; tenantId: string | null; role: string | null; actor: string };

function whoIs(w: World, headers: Record<string, string>): Who | null {
	const key = headers["x-api-key"];
	if (key) {
		const k = w.keys.get(key);
		if (!k) return null;
		return {
			admin: k.kind === "admin",
			tenantId: k.tenantId ?? null,
			role: k.kind === "tenant" ? "owner" : null,
			actor: "api_key_user",
		};
	}
	const auth = headers.authorization;
	if (!auth?.toLowerCase().startsWith("bearer ")) return null;
	const a = w.access.get(auth.slice(7));
	if (!a || a.expired) return null;
	const s = w.sessions.get(a.sid);
	const u = s ? w.users.find((x) => x.id === s.userId) : undefined;
	if (!s || s.revoked || !u) return null;
	return {
		admin: u.superadmin,
		tenantId: s.tenantId,
		role: u.memberships.find((m) => m.tenantId === s.tenantId)?.role ?? null,
		actor: u.email,
	};
}

const iso = (ms = Date.now()) => new Date(ms).toISOString();

/** "The AI": one chunk per paragraph (or sentence pairs), with a quality report. */
function structure(text: string): string[] {
	const paras = text
		.split(/\n\s*\n/)
		.map((p) => p.trim())
		.filter(Boolean);
	return paras.length > 1 ? paras : (text.match(/[^.!?]+[.!?]+/g) ?? [text]).map((s) => s.trim());
}

export async function installPortalMock(context: BrowserContext, world: World, s: PortalState): Promise<void> {
	await context.route(`${MOCK_ENGINE}/api/v1/portal/**`, async (route: Route) => {
		const req = route.request();
		const method = req.method();
		if (method === "OPTIONS") return route.fallback();
		const who = whoIs(world, await req.allHeaders());
		// Unauthenticated, expired or revoked: the engine mock answers (401 codes, refresh).
		if (!who) return route.fallback();
		const url = new URL(req.url());
		const path = url.pathname.replace("/api/v1/portal", "");
		const json = (status: number, body?: unknown) =>
			route.fulfill({
				status,
				headers: { ...CORS, "content-type": "application/json" },
				body: body === undefined ? "" : JSON.stringify(body),
			});
		const detail = (status: number, text: string, code?: string, extra: Record<string, unknown> = {}) =>
			json(status, { detail: text, ...(code ? { code } : {}), ...extra });
		let body: Record<string, unknown> = {};
		try {
			body = (req.postDataJSON() as Record<string, unknown>) ?? {};
		} catch {
			body = {};
		}
		if (method !== "GET") s.bodies.push({ path, body });
		const actor = (typeof body.user === "string" && body.user) || url.searchParams.get("user") || who.actor;
		const kbOf = (id: string) => world.kbs.items.find((k) => k.id === id);
		const canUse = (id: string | null | undefined) =>
			!!id && !!kbOf(id) && (who.admin || kbOf(id)?.tenant_id === who.tenantId);

		advanceAi(s);

		// ---- rules ----
		const rules = /^\/corpus\/([^/]+)\/rules(?:\/([^/]+))?$/.exec(path);
		if (rules) {
			const [, kb, ruleId] = rules;
			if (!canUse(kb)) return detail(403, "Corpus does not belong to this tenant.");
			if (method === "GET") {
				const items = s.rules.filter((r) => r.corpus_id === kb);
				return json(200, { items, count: items.length });
			}
			if (method === "POST") {
				const r: Rule = {
					id: randomUUID(),
					corpus_id: kb ?? "",
					rule_type: String(body.rule_type),
					condition: (body.condition as Record<string, unknown>) ?? {},
					action: String(body.action),
					priority: Number(body.priority ?? 100),
					enabled: body.enabled !== false,
					created_at: iso(),
				};
				s.rules.push(r);
				return json(201, r);
			}
			const r = s.rules.find((x) => x.id === ruleId);
			if (!r) return detail(404, "Rule not found");
			if (method === "PATCH") {
				Object.assign(r, body);
				return json(200, r);
			}
			if (method === "DELETE") {
				s.rules = s.rules.filter((x) => x.id !== ruleId);
				return json(204);
			}
		}

		// ---- workspaces: list, create, from-ingestion ----
		if (path === "/workspaces" && method === "GET") {
			const corpus = url.searchParams.get("corpus_id");
			if (corpus && !canUse(corpus)) return detail(403, "Corpus does not belong to this tenant.");
			const status = url.searchParams.get("status");
			const items = s.workspaces
				.filter((w) => (corpus ? w.corpus_id === corpus : canUse(w.corpus_id)) && (!status || w.status === status))
				.sort((a, b) => b.updated_at.localeCompare(a.updated_at))
				.map((w) => out(w, false));
			return json(200, { items, total: items.length, offset: 0, limit: 20 });
		}
		if (path === "/workspaces" && method === "POST") {
			const corpus = url.searchParams.get("corpus_id");
			if (!corpus) return json(422, { detail: [{ loc: ["query", "corpus_id"], msg: "Field required" }] });
			if (!canUse(corpus)) return detail(403, "Corpus does not belong to this tenant.");
			const w: Ws = {
				id: randomUUID(),
				corpus_id: corpus,
				title: String(body.title),
				kind: "standard",
				reviewed_version_id: null,
				finalized_version_id: null,
				status: "draft",
				workflow_mode: String(body.workflow_mode ?? "single_reviewer"),
				created_by: actor,
				assigned_reviewer: (body.assigned_reviewer as string | null) ?? null,
				locked_by: null,
				locked_at: null,
				review_feedback: null,
				created_at: iso(),
				updated_at: iso(),
				documentId: null,
			};
			s.workspaces.push(w);
			return json(201, out(w, false));
		}
		if (path === "/workspaces/from-ingestion" && method === "POST") {
			const versionId = String(body.document_version_id);
			const doc = world.docs.docs.find((d) => d.versions.some((v) => v.id === versionId));
			const version = doc?.versions.find((v) => v.id === versionId);
			if (!doc || !version) return detail(404, "Document version not found");
			if (!canUse(doc.corpusId)) return detail(403, "Corpus does not belong to this tenant.");
			if (version.status !== "active")
				return detail(409, `Document version ${versionId} is not the document's active version`, "version_not_active");
			const open = s.workspaces.find((w) => w.reviewed_version_id === versionId && w.status === "in_review");
			if (open)
				return detail(409, `A review workspace already exists for document version ${versionId}`, "review_exists", {
					workspace_id: open.id,
				});
			const w: Ws = {
				id: randomUUID(),
				corpus_id: doc.corpusId,
				title: String(body.title ?? `Post-ingest review: ${versionId}`),
				kind: "post_ingest_review",
				reviewed_version_id: versionId,
				finalized_version_id: null,
				status: "in_review",
				workflow_mode: "single_reviewer",
				created_by: actor,
				assigned_reviewer: null,
				locked_by: null,
				locked_at: null,
				review_feedback: null,
				created_at: iso(),
				updated_at: iso(),
				documentId: doc.id,
			};
			s.workspaces.push(w);
			for (const c of version.chunks) {
				s.chunks.push({
					id: randomUUID(),
					workspace_id: w.id,
					staged_document_id: null,
					text: c.text,
					original: c.text,
					chunk_index: c.chunk_index,
					metadata_: {
						engine_chunk_id: c.id,
						review_source: "post_ingest_review",
						parent_chunk_id: c.parent_chunk_id,
					},
					confidence_score: null,
					warnings: null,
					status: "approved",
					source: "from_ingestion",
					edit_history: [],
					created_at: iso(),
					updated_at: iso(),
				});
			}
			return json(200, {
				workspace_id: w.id,
				document_version_id: versionId,
				corpus_id: doc.corpusId,
				title: w.title,
				status: "in_review",
				chunks_copied: version.chunks.length,
				message: `Review workspace created with ${version.chunks.length} chunks`,
			});
		}

		// ---- one workspace ----
		const m = /^\/workspaces\/([^/]+)(\/.*)?$/.exec(path);
		if (!m) return detail(404, "Not Found");
		const w = s.workspaces.find((x) => x.id === m[1]);
		if (!w) return detail(404, "Workspace not found");
		if (!canUse(w.corpus_id)) return detail(403, "Corpus does not belong to this tenant.");
		const rest = m[2] ?? "";
		const chunksOf = () =>
			s.chunks.filter((c) => c.workspace_id === w.id).sort((a, b) => a.chunk_index - b.chunk_index);
		const reindex = () => {
			chunksOf().forEach((c, i) => {
				c.chunk_index = i;
			});
		};
		const touch = () => {
			w.updated_at = iso();
		};
		const lockRefusal = () => {
			if (!w.locked_by) return detail(409, `Workspace ${w.id} is not locked — acquire a lock first`);
			if (w.locked_by !== actor) return detail(409, `Workspace ${w.id} is locked by '${w.locked_by}', not '${actor}'`);
			return null;
		};
		const history = (c: SChunk, action: string, extra: Record<string, unknown> = {}) => {
			c.edit_history = [...c.edit_history, { user: actor, action, timestamp: iso(), ...extra }];
			c.updated_at = iso();
		};

		if (rest === "" && method === "GET") return json(200, out(w, true));
		if (rest === "" && method === "DELETE") {
			if (who.role === "member") return detail(403, "Only a workspace owner can do this", "owner_required");
			if (w.status !== "draft") return detail(409, `Can only delete draft workspaces, current: '${w.status}'`);
			s.workspaces = s.workspaces.filter((x) => x.id !== w.id);
			return json(204);
		}
		if (rest === "/lock") {
			if (w.status === "ingested") return detail(409, `Cannot lock workspace in '${w.status}' state`);
			if (w.locked_by && w.locked_by !== actor)
				return detail(423, `Workspace locked by '${w.locked_by}' until ${iso(Date.now() + 30 * 60_000)}`);
			w.locked_by = actor;
			w.locked_at = iso();
			return json(200, out(w, false));
		}
		if (rest === "/unlock") {
			if (w.locked_by && w.locked_by !== actor)
				return detail(423, `Lock held by '${w.locked_by}', cannot be released by '${actor}'`);
			w.locked_by = null;
			w.locked_at = null;
			return json(200, out(w, false));
		}

		// staging
		if (rest === "/content" && method === "GET") {
			const items = s.staged.filter((c) => c.workspace_id === w.id).map((c) => stagedOut(c));
			return json(200, { items, count: items.length });
		}
		if (rest === "/content" && method === "POST") {
			const ct = (await req.allHeaders())["content-type"] ?? "";
			let item: Staged;
			if (ct.includes("multipart")) {
				const raw = req.postDataBuffer()?.toString("utf8") ?? "";
				const name = /filename="([^"]+)"/.exec(raw)?.[1] ?? "upload.txt";
				const text = raw.split(/\r\n\r\n/)[1]?.split(/\r\n--/)[0] ?? "";
				item = staged(w.id, "file", name, "text/plain", text);
			} else {
				const kind = String(body.source_type);
				const content = String(body.content ?? "");
				if (kind === "url" && !/^https?:\/\//.test(content))
					return json(422, { detail: "URL content must start with http:// or https://" });
				item =
					kind === "url"
						? staged(w.id, "url", `${body.title ?? "url_source"}.url`, "text/uri-list", `Page at ${content}.`)
						: staged(w.id, "text_dump", `${body.title ?? "text_dump"}.txt`, "text/plain", content);
			}
			s.staged.push(item);
			touch();
			return json(201, stagedOut(item));
		}
		const reprocess = /^\/content\/([^/]+)\/reprocess$/.exec(rest);
		if (reprocess) {
			const c = s.staged.find((x) => x.id === reprocess[1] && x.workspace_id === w.id);
			if (!c) return detail(404, "Staged content not found");
			c.createdAt = Date.now();
			c.applied = false;
			return json(200, { content_id: c.id, ai_status: "pending", message: "AI transformation re-queued" });
		}

		// chunks
		if (rest === "/chunks" && method === "GET") {
			const status = url.searchParams.get("status");
			const items = chunksOf().filter((c) => !status || c.status === status);
			return json(200, { items: items.map(chunkOut), total: items.length, offset: 0, limit: 200 });
		}
		if (rest === "/chunks" && method === "POST") {
			const refused = lockRefusal();
			if (refused) return refused;
			const at = typeof body.chunk_index === "number" ? body.chunk_index - 0.5 : chunksOf().length;
			const c = newChunk(w.id, String(body.text), at, "manual");
			c.staged_document_id = (body.staged_document_id as string | null) ?? null;
			history(c, "create");
			s.chunks.push(c);
			reindex();
			touch();
			return json(201, chunkOut(c));
		}
		if (rest === "/chunks/merge" || rest === "/chunks/reorder") {
			const refused = lockRefusal();
			if (refused) return refused;
			const ids = (body.chunk_ids as string[]) ?? [];
			const list = chunksOf();
			if (rest === "/chunks/reorder") {
				for (const c of list) c.chunk_index = ids.indexOf(c.id);
				reindex();
				touch();
				return json(200, { items: chunksOf().map(chunkOut), total: list.length, offset: 0, limit: list.length });
			}
			const picked = list.filter((c) => ids.includes(c.id));
			const first = picked[0];
			if (!first || picked.length < 2) return detail(422, "At least 2 chunks are required for merge");
			const merged = newChunk(
				w.id,
				picked.map((c) => c.text).join(String(body.separator ?? "\n\n")),
				first.chunk_index,
				"manual",
			);
			history(merged, "merge", { old_text: picked.map((c) => c.text).join(" | "), new_text: merged.text });
			s.chunks = s.chunks.filter((c) => !ids.includes(c.id));
			s.chunks.push(merged);
			reindex();
			touch();
			return json(200, { merged: chunkOut(merged), deleted_chunk_ids: ids });
		}
		const one = /^\/chunks\/([^/]+)(?:\/(split|approve|reject))?$/.exec(rest);
		if (one) {
			const refused = lockRefusal();
			if (refused) return refused;
			const c = s.chunks.find((x) => x.id === one[1] && x.workspace_id === w.id);
			if (!c) return detail(404, `Chunk ${one[1]} not found in workspace ${w.id}`);
			const op = one[2];
			touch();
			if (method === "PATCH") {
				const old = c.text;
				if (typeof body.text === "string") c.text = body.text;
				history(c, "edit_text", { old_text: old, new_text: c.text });
				return json(200, chunkOut(c));
			}
			if (method === "DELETE") {
				s.chunks = s.chunks.filter((x) => x.id !== c.id);
				reindex();
				return json(204);
			}
			if (op === "approve" || op === "reject") {
				c.status = op === "approve" ? "approved" : "rejected";
				history(c, op, body.reason ? { details: { reason: String(body.reason) } } : {});
				return json(200, chunkOut(c));
			}
			if (op === "split") {
				const pos = Number(body.position);
				if (!(pos >= 1 && pos < c.text.length))
					return detail(422, `Split position ${pos} out of bounds for text length ${c.text.length}`);
				const a = newChunk(w.id, c.text.slice(0, pos).trimEnd(), c.chunk_index, "manual");
				const b = newChunk(w.id, c.text.slice(pos).trimStart(), c.chunk_index + 0.5, "manual");
				a.metadata_ = c.metadata_;
				b.metadata_ = c.metadata_;
				history(a, "split", { old_text: c.text, new_text: a.text, details: { position: pos, source_chunk_id: c.id } });
				history(b, "split", { new_text: b.text, details: { position: pos, source_chunk_id: c.id } });
				s.chunks = s.chunks.filter((x) => x.id !== c.id);
				s.chunks.push(a, b);
				reindex();
				return json(200, { first: chunkOut(a), second: chunkOut(b), deleted_chunk_id: c.id });
			}
		}

		// review
		const transition = (from: string, to: string) => {
			if (w.status !== from) return detail(409, `Invalid status transition: '${w.status}' → '${to}'`);
			return null;
		};
		if (rest === "/submit") {
			const bad = transition("draft", "in_review");
			if (bad) return bad;
			const list = chunksOf();
			if (!list.length) return detail(422, "Cannot submit empty workspace — at least one chunk is required");
			const rejected = list.filter((c) => c.status === "rejected").length;
			if (rejected) return detail(422, `Cannot submit workspace with ${rejected} rejected chunk(s).`);
			w.status = "in_review";
			w.locked_by = null;
			w.locked_at = null;
			touch();
			return json(200, {
				workspace_id: w.id,
				status: "in_review",
				message: "Submitted for review",
				total_chunks: list.length,
				approved_chunks: list.filter((c) => c.status === "approved").length,
				rejected_chunks: 0,
				draft_chunks: list.filter((c) => c.status === "draft").length,
			});
		}
		if (rest === "/approve" || rest === "/reject") {
			if (w.kind === "post_ingest_review")
				return detail(409, "This is a post-ingest review workspace", "wrong_workspace_kind");
			const bad = transition("in_review", rest === "/approve" ? "approved" : "draft");
			if (bad) return bad;
			w.status = rest === "/approve" ? "approved" : "draft";
			if (rest === "/reject") w.review_feedback = String(body.feedback ?? "");
			touch();
			return json(200, { workspace_id: w.id, status: w.status, message: "ok", review_feedback: w.review_feedback });
		}
		if (rest === "/ingest") {
			if (w.kind === "post_ingest_review")
				return detail(409, "This is a post-ingest review workspace", "wrong_workspace_kind");
			const tenant = world.docs.tenantOf(w.corpus_id);
			const limit = tenant ? world.docs.limits.get(tenant) : undefined;
			const used = world.docs.docs.filter(
				(d) => world.docs.tenantOf(d.corpusId) === tenant && d.versions.some((v) => v.status === "active"),
			).length;
			if (limit != null && used + 1 > limit)
				return detail(409, `Document limit reached (${limit})`, "document_limit_reached", { limit, used, adding: 1 });
			const bad = transition("approved", "ingested");
			if (bad) return bad;
			const list = chunksOf().filter((c) => c.status !== "rejected");
			const doc: DocRecord = {
				id: randomUUID(),
				corpusId: w.corpus_id,
				file_name: `${w.title}.md`,
				mime_type: "text/markdown",
				size_bytes: list.reduce((n, c) => n + c.text.length, 0),
				ingested_at: iso(),
				external_id: null,
				metadata: { source: "content_portal" },
				source_uri: null,
				versions: [version(list.map((c) => c.text))],
			};
			world.docs.docs.push(doc);
			world.docs.onChange(w.corpus_id);
			w.status = "ingested";
			w.locked_by = null;
			touch();
			return json(200, {
				workspace_id: w.id,
				status: "ingested",
				message: "Ingestion started",
				chunks_ingested: list.length,
				job_id: randomUUID(),
			});
		}
		if (rest === "/audit-trail") {
			const entries = chunksOf()
				.flatMap((c) =>
					c.edit_history.map((e) => ({
						timestamp: (e.timestamp as string | undefined) ?? null,
						user: e.user,
						action: e.action,
						// An object since engine 0e1260a (a string on older entries).
						details: (e.details as Record<string, unknown> | string | undefined) ?? null,
						old_text: (e.old_text as string | undefined) ?? null,
						new_text: (e.new_text as string | undefined) ?? null,
						chunk_id: c.id,
					})),
				)
				// Newest first; entries without a timestamp last (engine 0e1260a).
				.sort((a, b) => String(b.timestamp ?? "").localeCompare(String(a.timestamp ?? "")));
			return json(200, { workspace_id: w.id, entries, total: entries.length });
		}

		// chat (non-streaming): proposes a rewrite of the shortest chunk
		if (rest === "/chat/history") {
			const messages = s.chat[w.id] ?? [];
			return json(200, { workspace_id: w.id, conversation_id: null, messages, total: messages.length });
		}
		if (rest === "/chat") {
			const shortest = [...chunksOf()].sort((a, b) => a.text.length - b.text.length)[0];
			const list = s.chat[w.id] ?? [];
			list.push({ id: randomUUID(), role: "user", content: String(body.message), created_at: iso() });
			const reply = {
				id: randomUUID(),
				role: "assistant",
				content: shortest
					? `Chunk ${shortest.chunk_index + 1} is short. I suggest rewriting it with the timings spelled out.`
					: "There are no chunks yet.",
				tool_calls: shortest
					? [
							{
								tool_name: "rewrite_chunk",
								arguments: { chunk_id: shortest.id, new_text: `${shortest.text} (Monday to Saturday.)` },
							},
						]
					: null,
				token_usage: { total_tokens: 740 },
				created_at: iso(),
			};
			list.push(reply);
			s.chat[w.id] = list;
			return json(200, { ...reply, message_id: reply.id, conversation_id: randomUUID() });
		}
		if (rest === "/chat/execute") {
			const action = body.action as { tool_name: string; arguments: Record<string, unknown> };
			const c = s.chunks.find((x) => x.id === action.arguments.chunk_id && x.workspace_id === w.id);
			if (!c) return detail(404, "Chunk not found in workspace");
			const old = c.text;
			c.text = String(action.arguments.new_text ?? c.text);
			history(c, "rewrite", { old_text: old, new_text: c.text });
			return json(200, { action: action.tool_name, success: true, message: "Chunk rewritten" });
		}

		// post-ingest review
		if (rest === "/review-status") {
			const list = chunksOf();
			return json(200, {
				workspace_id: w.id,
				kind: w.kind,
				document_version_id: w.reviewed_version_id,
				finalized_version_id: w.finalized_version_id,
				corpus_id: w.corpus_id,
				status: w.status,
				total_chunks: list.length,
				approved_chunks: list.filter((c) => c.status === "approved").length,
				rejected_chunks: list.filter((c) => c.status === "rejected").length,
				draft_chunks: list.filter((c) => c.status === "draft").length,
				modified_chunks: list.filter((c) => c.original !== undefined && c.original !== c.text).length,
			});
		}
		if (rest === "/finalize-review") {
			if (w.kind !== "post_ingest_review")
				return detail(409, "finalize-review publishes a review of an ingested document", "wrong_workspace_kind");
			if (w.status !== "in_review")
				return detail(409, `Workspace ${w.id} is not in review (status=${w.status})`, "invalid_status_transition");
			if (w.locked_by && w.locked_by !== actor)
				return detail(409, `Workspace is locked by '${w.locked_by}'; they must unlock it`, "workspace_locked");
			const doc = world.docs.docs.find((d) => d.id === w.documentId);
			const reviewed = doc?.versions.find((v) => v.id === w.reviewed_version_id);
			if (!doc || !reviewed)
				return detail(409, "The document this workspace reviews was deleted", "reviewed_document_deleted");
			if (reviewed.status !== "active")
				return detail(409, "The reviewed version is no longer the document's active version", "version_not_active");
			const kept = chunksOf().filter((c) => c.status !== "rejected");
			if (!kept.length) return detail(422, "Every chunk was rejected or deleted", "nothing_to_finalize");
			for (const v of doc.versions) v.status = "inactive";
			const next = version(kept.map((c) => c.text));
			doc.versions.push(next);
			world.docs.onChange(w.corpus_id);
			w.status = "ingested";
			w.finalized_version_id = next.id;
			w.locked_by = null;
			touch();
			const unchanged = kept.filter((c) => c.original === c.text).length;
			return json(200, {
				workspace_id: w.id,
				status: "ingested",
				message: "Published",
				document_id: doc.id,
				previous_version_id: reviewed.id,
				new_version_id: next.id,
				chunks_in_new_version: kept.length,
				chunks_unchanged: unchanged,
				chunks_edited: kept.filter((c) => c.original !== undefined && c.original !== c.text).length,
				chunks_added: kept.filter((c) => c.original === undefined).length,
				parent_chunks: 0,
				staged_chunks_rejected: chunksOf().length - kept.length,
				previous_chunks_dropped: Math.max(0, reviewed.chunks.length - unchanged - 1),
				staged_chunks_total: chunksOf().length,
				discarded_parent_edits: [],
			});
		}
		return detail(404, "Not Found");
	});

	function staged(workspaceId: string, source: string, name: string, mime: string, text: string): Staged {
		return {
			id: randomUUID(),
			workspace_id: workspaceId,
			source_type: source,
			original_filename: name,
			mime_type: mime,
			text,
			createdAt: Date.now(),
			applied: false,
		};
	}

	function out(w: Ws, stats: boolean) {
		const { documentId: _d, ...rest } = w;
		const chunks = s.chunks.filter((c) => c.workspace_id === w.id);
		return {
			...rest,
			content_count: stats ? s.staged.filter((c) => c.workspace_id === w.id).length : null,
			chunk_count: stats ? chunks.length : null,
			approved_chunk_count: stats ? chunks.filter((c) => c.status === "approved").length : null,
		};
	}
}

function aiStatus(c: Staged): string {
	const age = Date.now() - c.createdAt;
	return age < AI_MS / 3 ? "pending" : age < AI_MS ? "processing" : "completed";
}

function stagedOut(c: Staged) {
	const status = aiStatus(c);
	return {
		id: c.id,
		workspace_id: c.workspace_id,
		source_type: c.source_type,
		original_filename: c.original_filename,
		mime_type: c.mime_type,
		raw_storage_key: `portal/${c.workspace_id}/${c.id}`,
		ai_status: status,
		ai_output_type: status === "completed" ? "policy" : null,
		ai_quality_report: status === "completed" ? { overall: "good", duplicates: 0, missing_context: 0 } : null,
		token_usage: status === "completed" ? { total_tokens: 1240, steps: {} } : null,
		created_at: iso(c.createdAt),
	};
}

/** Structured items become chunks once (the AI task's output). */
function advanceAi(s: PortalState) {
	for (const c of s.staged) {
		if (c.applied || aiStatus(c) !== "completed") continue;
		c.applied = true;
		const docId = randomUUID();
		const start = s.chunks.filter((x) => x.workspace_id === c.workspace_id).length;
		structure(c.text).forEach((text, i) => {
			const ch = newChunk(c.workspace_id, text, start + i, "ai_generated");
			ch.staged_document_id = docId;
			ch.confidence_score = text.length < 40 ? 0.55 : 0.9;
			ch.warnings = text.length < 40 ? { issues: ["Short chunk: may lack context"] } : null;
			ch.metadata_ = { section: "Home collection" };
			s.chunks.push(ch);
		});
	}
}

function newChunk(workspaceId: string, text: string, index: number, source: string): SChunk {
	return {
		id: randomUUID(),
		workspace_id: workspaceId,
		staged_document_id: null,
		text,
		chunk_index: index,
		metadata_: null,
		confidence_score: null,
		warnings: null,
		status: "draft",
		source,
		edit_history: [],
		created_at: iso(),
		updated_at: iso(),
	};
}

function chunkOut(c: SChunk) {
	const { original: _o, ...rest } = c;
	return rest;
}

function version(lines: string[]): DocRecord["versions"][number] {
	return {
		id: randomUUID(),
		status: "active",
		parser_version: "portal",
		chunk_schema_version: "2",
		created_at: iso(),
		chunks: lines.map((text, i) => ({
			id: randomUUID(),
			chunk_index: i,
			text,
			page: null,
			token_count: Math.ceil(text.length / 4),
			is_table: false,
			is_code: false,
			parent_chunk_id: null,
			section: ["Home collection"],
		})),
	};
}

/** A draft workspace with a few AI chunks (for the gating and chat journeys). */
export function seedDraft(s: PortalState, corpusId: string, title: string, createdBy: string, lines: string[]): string {
	const id = randomUUID();
	s.workspaces.push({
		id,
		corpus_id: corpusId,
		title,
		kind: "standard",
		reviewed_version_id: null,
		finalized_version_id: null,
		status: "draft",
		workflow_mode: "single_reviewer",
		created_by: createdBy,
		assigned_reviewer: null,
		locked_by: null,
		locked_at: null,
		review_feedback: null,
		created_at: iso(),
		updated_at: iso(),
		documentId: null,
	});
	lines.forEach((text, i) => {
		const c = newChunk(id, text, i, "ai_generated");
		c.confidence_score = 0.88;
		s.chunks.push(c);
	});
	return id;
}
