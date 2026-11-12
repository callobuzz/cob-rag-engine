import { HttpResponse, http } from "msw";
import type { ChatMessage, Chunk, Rule, StagedContent, Workspace } from "@/features/portal/api";
import { BASE, CORPORA, PRIYA } from "../data";
import { INFO } from "./identity";

/**
 * Content Portal (D11), from content_portal/routes/*.py: workspaces, lock (423 when someone else
 * holds it; chunk edits refused with an uncoded 409 without the lock), staging (text/url JSON or a
 * file), chunks, review transitions, chat, audit trail, post-ingest review and routing rules.
 * Stateful within a test: `resetPortalState()` before each (the portal test file does it; setup.ts
 * is shared). Every request is logged in `portalState.requests` for body assertions.
 * Indian demo data only.
 */
export const PATIENT = CORPORA[0]?.id ?? "";
export const WS_DRAFT = "9a000001-0000-4000-8000-000000000001";
export const WS_REVIEW = "9a000002-0000-4000-8000-000000000002";
export const DOC_VERSION = "7e500002-0000-4000-8000-000000000002";
export const DOC_ID = "d0c00001-0000-4000-8000-000000000001";
export const CH = (i: number) => `c1000000-0000-4000-8000-${String(i).padStart(12, "0")}`;
export const STAGED_DOC = "5d000001-0000-4000-8000-000000000001";

const now = "2026-09-30T10:42:00Z";

function ws(id: string, title: string, extra: Partial<Workspace> = {}): Workspace {
	return {
		id,
		corpus_id: PATIENT,
		title,
		kind: "standard",
		reviewed_version_id: null,
		finalized_version_id: null,
		status: "draft",
		workflow_mode: "single_reviewer",
		created_by: PRIYA.email,
		assigned_reviewer: null,
		locked_by: null,
		locked_at: null,
		review_feedback: null,
		created_at: "2026-09-29T09:00:00Z",
		updated_at: now,
		content_count: 1,
		chunk_count: 3,
		approved_chunk_count: 1,
		...extra,
	};
}

function chunk(ws: string, i: number, text: string, extra: Partial<Chunk> = {}): Chunk {
	return {
		id: CH(ws === WS_REVIEW ? 100 + i : i),
		workspace_id: ws,
		staged_document_id: ws === WS_DRAFT ? STAGED_DOC : null,
		text,
		chunk_index: i,
		metadata_: { section: "Home collection" },
		confidence_score: 0.82,
		warnings: null,
		status: "draft",
		source: ws === WS_REVIEW ? "from_ingestion" : "ai_generated",
		edit_history: [],
		created_at: now,
		updated_at: now,
		...extra,
	};
}

interface State {
	workspaces: Workspace[];
	content: Record<string, StagedContent[]>;
	chunks: Record<string, Chunk[]>;
	chat: Record<string, ChatMessage[]>;
	rules: Rule[];
	requests: { method: string; path: string; body: unknown; query: string }[];
	/** Who a session is recorded as (the engine uses the signed-in e-mail). */
	sessionEmail: string;
	/** Answer the next finalize with this error. */
	finalizeError: { status: number; body: Record<string, unknown> } | null;
	/** Answer the next from-ingestion with this error. */
	fromIngestionError: { status: number; body: Record<string, unknown> } | null;
	/** AI status reported for new staged items on each list call (pending → completed). */
	aiSteps: string[];
	seq: number;
}

function seed(): State {
	return {
		workspaces: [
			ws(WS_DRAFT, "Home collection policy"),
			ws(WS_REVIEW, "Review: test-price-list-oct.pdf", {
				kind: "post_ingest_review",
				reviewed_version_id: DOC_VERSION,
				status: "in_review",
				content_count: 0,
				chunk_count: 2,
				approved_chunk_count: 2,
			}),
		],
		content: {
			[WS_DRAFT]: [
				{
					id: "5c000001-0000-4000-8000-000000000001",
					workspace_id: WS_DRAFT,
					source_type: "text_dump",
					original_filename: "Home collection rules.txt",
					mime_type: "text/plain",
					raw_storage_key: "portal/x/y/z",
					ai_status: "completed",
					ai_output_type: "faq",
					ai_quality_report: { overall: "good", duplicates: 0 },
					token_usage: { total_tokens: 1840, steps: {} },
					created_at: now,
				},
			],
			[WS_REVIEW]: [],
		},
		chunks: {
			[WS_DRAFT]: [
				chunk(
					WS_DRAFT,
					0,
					"Home collection is available in Kothrud, Baner and Aundh from 7 am to 11 am. Book by phone on +91 20 2545 1234.",
				),
				chunk(WS_DRAFT, 1, "A home visit costs ₹150 on top of the test price. Senior citizens pay no visit fee.", {
					status: "approved",
				}),
				chunk(WS_DRAFT, 2, "Fasting samples are collected before 9 am.", {
					warnings: { issues: ["Very short chunk"] },
				}),
			],
			[WS_REVIEW]: [
				chunk(WS_REVIEW, 0, "CBC (complete blood count): ₹350.", { status: "approved" }),
				chunk(WS_REVIEW, 1, "Thyroid profile (T3, T4, TSH): ₹550.", { status: "approved" }),
			],
		},
		chat: { [WS_DRAFT]: [], [WS_REVIEW]: [] },
		rules: [
			{
				id: "7a000001-0000-4000-8000-000000000001",
				corpus_id: PATIENT,
				rule_type: "mime_type",
				condition: { mime_type: "application/pdf" },
				action: "stage",
				priority: 10,
				enabled: true,
				created_at: now,
			},
		],
		requests: [],
		sessionEmail: PRIYA.email,
		finalizeError: null,
		fromIngestionError: null,
		aiSteps: ["processing", "completed"],
		seq: 0,
	};
}

export let portalState: State = seed();
export function resetPortalState(): void {
	portalState = seed();
}

const P = `${BASE}/api/v1/portal`;
type Body = Record<string, unknown>;

async function read(request: Request): Promise<Body> {
	const text = await request.clone().text();
	if (!text) return {};
	try {
		return JSON.parse(text) as Body;
	} catch {
		return { raw: text };
	}
}

async function log(request: Request): Promise<Body> {
	const url = new URL(request.url);
	const body = request.method === "GET" || request.method === "DELETE" ? {} : await read(request);
	portalState.requests.push({ method: request.method, path: url.pathname, body, query: url.search });
	return body;
}

const find = (id: unknown) => portalState.workspaces.find((w) => w.id === id);
const notFound = () => HttpResponse.json({ detail: "Workspace not found" }, { status: 404 });
const actorOf = (body: Body, url?: URL) =>
	(typeof body.user === "string" && body.user) || url?.searchParams.get("user") || portalState.sessionEmail;

function stats(w: Workspace): Workspace {
	const chunks = portalState.chunks[w.id] ?? [];
	return {
		...w,
		content_count: (portalState.content[w.id] ?? []).length,
		chunk_count: chunks.length,
		approved_chunk_count: chunks.filter((c) => c.status === "approved").length,
	};
}

/** The chunk editor's lock check (service `_verify_workspace_lock`): an uncoded 409. */
function lockRefusal(w: Workspace, actor: string) {
	if (!w.locked_by)
		return HttpResponse.json({ detail: `Workspace ${w.id} is not locked — acquire a lock first` }, { status: 409 });
	if (w.locked_by !== actor)
		return HttpResponse.json(
			{ detail: `Workspace ${w.id} is locked by '${w.locked_by}', not '${actor}'` },
			{ status: 409 },
		);
	return null;
}

function reindex(id: string) {
	const list = portalState.chunks[id] ?? [];
	list.sort((a, b) => a.chunk_index - b.chunk_index);
	list.forEach((c, i) => {
		c.chunk_index = i;
	});
}

function newId() {
	portalState.seq++;
	return `c2000000-0000-4000-8000-${String(portalState.seq).padStart(12, "0")}`;
}

export const portalInfo = http.get(`${BASE}/api/v1/info`, () =>
	HttpResponse.json({ ...INFO, features: { ...INFO.features, content_portal: true } }),
);

export const portalHandlers = [
	portalInfo,
	// ---- workspaces ----
	http.get(`${P}/workspaces`, async ({ request }) => {
		await log(request);
		const url = new URL(request.url);
		const corpus = url.searchParams.get("corpus_id");
		const status = url.searchParams.get("status");
		const items = portalState.workspaces
			.filter((w) => (!corpus || w.corpus_id === corpus) && (!status || w.status === status))
			// The list carries no counts (the engine fills them only on the detail).
			.map((w) => ({ ...w, content_count: null, chunk_count: null, approved_chunk_count: null }));
		return HttpResponse.json({ items, total: items.length, offset: 0, limit: 20 });
	}),
	http.post(`${P}/workspaces/from-ingestion`, async ({ request }) => {
		const body = await log(request);
		const err = portalState.fromIngestionError;
		if (err) {
			portalState.fromIngestionError = null;
			return HttpResponse.json(err.body, { status: err.status });
		}
		const id = "9a000003-0000-4000-8000-000000000003";
		portalState.workspaces.push(
			ws(id, String(body.title ?? "Post-ingest review"), {
				kind: "post_ingest_review",
				reviewed_version_id: String(body.document_version_id),
				status: "in_review",
				created_by: actorOf(body),
			}),
		);
		portalState.chunks[id] = [chunk(id, 0, "CBC (complete blood count): ₹350.", { status: "approved" })];
		return HttpResponse.json({
			workspace_id: id,
			document_version_id: body.document_version_id,
			corpus_id: PATIENT,
			title: body.title,
			status: "in_review",
			chunks_copied: 1,
			message: "Review workspace created with 1 chunks",
		});
	}),
	http.post(`${P}/workspaces`, async ({ request }) => {
		const body = await log(request);
		const corpus = new URL(request.url).searchParams.get("corpus_id");
		if (!corpus) return HttpResponse.json({ detail: "corpus_id is required" }, { status: 422 });
		const id = `9a0000ff-0000-4000-8000-${String(portalState.workspaces.length + 1).padStart(12, "0")}`;
		const w = ws(id, String(body.title), {
			corpus_id: corpus,
			workflow_mode: String(body.workflow_mode ?? "single_reviewer"),
			assigned_reviewer: (body.assigned_reviewer as string | null) ?? null,
			created_by: actorOf(body),
			content_count: 0,
			chunk_count: 0,
			approved_chunk_count: 0,
		});
		portalState.workspaces.push(w);
		portalState.content[id] = [];
		portalState.chunks[id] = [];
		portalState.chat[id] = [];
		return HttpResponse.json(w, { status: 201 });
	}),
	http.get(`${P}/workspaces/:id`, async ({ request, params }) => {
		await log(request);
		const w = find(params.id);
		return w ? HttpResponse.json(stats(w)) : notFound();
	}),
	http.delete(`${P}/workspaces/:id`, async ({ request, params }) => {
		await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		if (w.status !== "draft")
			return HttpResponse.json({ detail: `Can only delete draft workspaces, current: '${w.status}'` }, { status: 409 });
		portalState.workspaces = portalState.workspaces.filter((x) => x.id !== w.id);
		return new HttpResponse(null, { status: 204 });
	}),
	http.post(`${P}/workspaces/:id/lock`, async ({ request, params }) => {
		const body = await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		const actor = actorOf(body);
		if (w.locked_by && w.locked_by !== actor)
			return HttpResponse.json(
				{ detail: `Workspace locked by '${w.locked_by}' until 2026-09-30T11:12:00+00:00` },
				{ status: 423 },
			);
		w.locked_by = actor;
		w.locked_at = new Date().toISOString();
		return HttpResponse.json({ ...w, content_count: null, chunk_count: null, approved_chunk_count: null });
	}),
	http.post(`${P}/workspaces/:id/unlock`, async ({ request, params }) => {
		const body = await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		const actor = actorOf(body);
		if (w.locked_by && w.locked_by !== actor)
			return HttpResponse.json(
				{ detail: `Lock held by '${w.locked_by}', cannot be released by '${actor}'` },
				{ status: 423 },
			);
		w.locked_by = null;
		w.locked_at = null;
		return HttpResponse.json(w);
	}),
	// ---- staging ----
	http.get(`${P}/workspaces/:id/content`, async ({ request, params }) => {
		await log(request);
		const items = portalState.content[String(params.id)] ?? [];
		for (const c of items) {
			if (c.ai_status !== "completed" && c.ai_status !== "failed") {
				const next = portalState.aiSteps.shift();
				if (next) c.ai_status = next;
				if (c.ai_status === "completed") {
					c.ai_output_type = "faq";
					const list = portalState.chunks[String(params.id)] ?? [];
					list.push(chunk(String(params.id), list.length, "Samples reach the lab within 2 hours."));
					portalState.chunks[String(params.id)] = list;
				}
			}
		}
		return HttpResponse.json({ items, count: items.length });
	}),
	http.post(`${P}/workspaces/:id/content`, async ({ request, params }) => {
		const id = String(params.id);
		const type = request.headers.get("content-type") ?? "";
		const body = await log(request);
		const source = type.includes("json") ? String(body.source_type) : "file";
		if (source === "url" && !/^https?:\/\//.test(String(body.content)))
			return HttpResponse.json({ detail: "URL content must start with http:// or https://" }, { status: 422 });
		const item: StagedContent = {
			id: `5c0000ff-0000-4000-8000-${String((portalState.content[id]?.length ?? 0) + 1).padStart(12, "0")}`,
			workspace_id: id,
			source_type: source === "file" ? "file" : source,
			original_filename:
				source === "file"
					? "home-collection.pdf"
					: source === "url"
						? `${body.title ?? "url_source"}.url`
						: `${body.title ?? "text_dump"}.txt`,
			mime_type: source === "file" ? "application/pdf" : source === "url" ? "text/uri-list" : "text/plain",
			raw_storage_key: "portal/x",
			ai_status: "pending",
			ai_output_type: null,
			ai_quality_report: null,
			token_usage: null,
			created_at: new Date().toISOString(),
		};
		portalState.content[id] = [...(portalState.content[id] ?? []), item];
		return HttpResponse.json(item, { status: 201 });
	}),
	http.post(`${P}/workspaces/:id/content/:cid/reprocess`, async ({ request, params }) => {
		await log(request);
		const c = (portalState.content[String(params.id)] ?? []).find((x) => x.id === params.cid);
		if (!c) return HttpResponse.json({ detail: "Staged content not found" }, { status: 404 });
		c.ai_status = "pending";
		return HttpResponse.json({ content_id: c.id, ai_status: "pending", message: "AI transformation re-queued" });
	}),
	// ---- chunks ----
	http.get(`${P}/workspaces/:id/chunks`, async ({ request, params }) => {
		await log(request);
		const url = new URL(request.url);
		const status = url.searchParams.get("status");
		const items = (portalState.chunks[String(params.id)] ?? [])
			.filter((c) => !status || c.status === status)
			.sort((a, b) => a.chunk_index - b.chunk_index);
		return HttpResponse.json({ items, total: items.length, offset: 0, limit: 200 });
	}),
	http.post(`${P}/workspaces/:id/chunks/merge`, async ({ request, params }) => {
		const body = await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		const refused = lockRefusal(w, actorOf(body));
		if (refused) return refused;
		const ids = body.chunk_ids as string[];
		const list = portalState.chunks[w.id] ?? [];
		const picked = list.filter((c) => ids.includes(c.id)).sort((a, b) => a.chunk_index - b.chunk_index);
		const first = picked[0];
		if (!first || picked.length < 2)
			return HttpResponse.json({ detail: "At least 2 chunks are required for merge" }, { status: 422 });
		const merged: Chunk = { ...first, id: newId(), text: picked.map((c) => c.text).join(String(body.separator)) };
		portalState.chunks[w.id] = [...list.filter((c) => !ids.includes(c.id)), merged];
		reindex(w.id);
		return HttpResponse.json({ merged, deleted_chunk_ids: ids });
	}),
	http.post(`${P}/workspaces/:id/chunks/reorder`, async ({ request, params }) => {
		const body = await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		const refused = lockRefusal(w, actorOf(body));
		if (refused) return refused;
		const ids = body.chunk_ids as string[];
		for (const c of portalState.chunks[w.id] ?? []) c.chunk_index = ids.indexOf(c.id);
		reindex(w.id);
		const items = portalState.chunks[w.id] ?? [];
		return HttpResponse.json({ items, total: items.length, offset: 0, limit: items.length });
	}),
	http.post(`${P}/workspaces/:id/chunks`, async ({ request, params }) => {
		const body = await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		if (body.staged_document_id && body.staged_document_id !== STAGED_DOC)
			return HttpResponse.json({ detail: "Staged document not found in this workspace" }, { status: 404 });
		const refused = lockRefusal(w, actorOf(body));
		if (refused) return refused;
		const list = portalState.chunks[w.id] ?? [];
		const at = typeof body.chunk_index === "number" ? body.chunk_index - 0.5 : list.length;
		const c = chunk(w.id, 0, String(body.text), {
			id: newId(),
			chunk_index: at,
			source: "manual",
			staged_document_id: (body.staged_document_id as string | null) ?? null,
		});
		portalState.chunks[w.id] = [...list, c];
		reindex(w.id);
		return HttpResponse.json(c, { status: 201 });
	}),
	http.patch(`${P}/workspaces/:id/chunks/:cid`, async ({ request, params }) => {
		const body = await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		const refused = lockRefusal(w, actorOf(body));
		if (refused) return refused;
		const c = (portalState.chunks[w.id] ?? []).find((x) => x.id === params.cid);
		if (!c) return HttpResponse.json({ detail: "Chunk not found" }, { status: 404 });
		if (typeof body.text === "string") c.text = body.text;
		return HttpResponse.json(c);
	}),
	http.delete(`${P}/workspaces/:id/chunks/:cid`, async ({ request, params }) => {
		await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		const refused = lockRefusal(w, actorOf({}, new URL(request.url)));
		if (refused) return refused;
		portalState.chunks[w.id] = (portalState.chunks[w.id] ?? []).filter((x) => x.id !== params.cid);
		reindex(w.id);
		return new HttpResponse(null, { status: 204 });
	}),
	http.post(`${P}/workspaces/:id/chunks/:cid/:op`, async ({ request, params }) => {
		const body = await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		const refused = lockRefusal(w, actorOf(body));
		if (refused) return refused;
		const list = portalState.chunks[w.id] ?? [];
		const c = list.find((x) => x.id === params.cid);
		if (!c) return HttpResponse.json({ detail: `Chunk ${params.cid} not found` }, { status: 404 });
		if (params.op === "approve" || params.op === "reject") {
			c.status = params.op === "approve" ? "approved" : "rejected";
			return HttpResponse.json(c);
		}
		if (params.op === "split") {
			const pos = Number(body.position);
			if (pos < 1 || pos >= c.text.length)
				return HttpResponse.json({ detail: `Split position ${pos} out of bounds` }, { status: 422 });
			const first: Chunk = { ...c, id: newId(), text: c.text.slice(0, pos).trimEnd() };
			const second: Chunk = {
				...c,
				id: newId(),
				text: c.text.slice(pos).trimStart(),
				chunk_index: c.chunk_index + 0.5,
			};
			portalState.chunks[w.id] = [...list.filter((x) => x.id !== c.id), first, second];
			reindex(w.id);
			return HttpResponse.json({ first, second, deleted_chunk_id: c.id });
		}
		return HttpResponse.json({ detail: "Not Found" }, { status: 404 });
	}),
	// ---- review ----
	http.post(`${P}/workspaces/:id/submit`, async ({ request, params }) => {
		await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		if (w.status !== "draft")
			return HttpResponse.json({ detail: `Invalid transition: '${w.status}' → 'in_review'` }, { status: 409 });
		w.status = "in_review";
		w.locked_by = null;
		w.locked_at = null;
		const chunks = portalState.chunks[w.id] ?? [];
		return HttpResponse.json({
			workspace_id: w.id,
			status: "in_review",
			message: "Submitted",
			total_chunks: chunks.length,
			approved_chunks: chunks.filter((c) => c.status === "approved").length,
			rejected_chunks: 0,
			draft_chunks: chunks.filter((c) => c.status === "draft").length,
		});
	}),
	http.post(`${P}/workspaces/:id/approve`, async ({ request, params }) => {
		await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		if (w.kind === "post_ingest_review")
			return HttpResponse.json(
				{ detail: "approve doesn't apply to a review workspace", code: "wrong_workspace_kind" },
				{ status: 409 },
			);
		w.status = "approved";
		return HttpResponse.json({ workspace_id: w.id, status: "approved", message: "Approved" });
	}),
	http.post(`${P}/workspaces/:id/reject`, async ({ request, params }) => {
		const body = await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		w.status = "draft";
		w.review_feedback = String(body.feedback);
		return HttpResponse.json({
			workspace_id: w.id,
			status: "draft",
			message: "Rejected",
			review_feedback: body.feedback,
		});
	}),
	http.post(`${P}/workspaces/:id/ingest`, async ({ request, params }) => {
		await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		if (w.status !== "approved")
			return HttpResponse.json({ detail: `Invalid transition: '${w.status}' → 'ingested'` }, { status: 409 });
		w.status = "ingested";
		return HttpResponse.json({
			workspace_id: w.id,
			status: "ingested",
			message: "Ingestion started",
			chunks_ingested: (portalState.chunks[w.id] ?? []).length,
			job_id: "10b00001-0000-4000-8000-000000000001",
		});
	}),
	http.get(`${P}/workspaces/:id/audit-trail`, async ({ request, params }) => {
		await log(request);
		return HttpResponse.json({
			workspace_id: params.id,
			// An older AI chat entry without a timestamp (engine 0e1260a), out of place on purpose: the
			// dashboard lists such entries last.
			entries: [
				{
					timestamp: now,
					user: PRIYA.email,
					action: "edit_text",
					details: null,
					old_text: "Home collection 7–11.",
					new_text: "Home collection is available from 7 am to 11 am.",
					chunk_id: CH(0),
				},
				{
					timestamp: null,
					user: PRIYA.email,
					action: "merged_from",
					details: { source_chunk_ids: [CH(0), CH(1)] },
					old_text: null,
					new_text: null,
					chunk_id: CH(0),
				},
				{
					timestamp: "2026-09-30T10:30:00Z",
					user: PRIYA.email,
					action: "split",
					details: { position: 120, source_chunk_id: CH(1) },
					old_text: null,
					new_text: null,
					chunk_id: CH(2),
				},
			],
			total: 3,
		});
	}),
	// ---- chat ----
	http.get(`${P}/workspaces/:id/chat/history`, async ({ request, params }) => {
		await log(request);
		const messages = portalState.chat[String(params.id)] ?? [];
		return HttpResponse.json({ workspace_id: params.id, conversation_id: null, messages, total: messages.length });
	}),
	http.post(`${P}/workspaces/:id/chat/execute`, async ({ request, params }) => {
		const body = await log(request);
		const action = body.action as { tool_name: string; arguments: Record<string, unknown> };
		const c = (portalState.chunks[String(params.id)] ?? []).find((x) => x.id === action.arguments.chunk_id);
		if (!c) return HttpResponse.json({ detail: "Chunk not found in workspace" }, { status: 404 });
		c.text = String(action.arguments.new_text ?? c.text);
		return HttpResponse.json({ action: action.tool_name, success: true, message: "Chunk rewritten" });
	}),
	http.post(`${P}/workspaces/:id/chat`, async ({ request, params }) => {
		const body = await log(request);
		const id = String(params.id);
		const list = portalState.chat[id] ?? [];
		list.push({ id: newId(), role: "user", content: String(body.message), created_at: now });
		const reply: ChatMessage = {
			id: newId(),
			role: "assistant",
			content: 'Chunk 3 is very short. I suggest a rewrite:\n```tool_call\n{"tool_name": "rewrite_chunk"}\n```',
			tool_calls: [
				{
					tool_name: "rewrite_chunk",
					arguments: {
						chunk_id: CH(2),
						new_text: "Fasting samples are collected before 9 am; please fast for 10–12 hours.",
					},
				},
			],
			token_usage: { total_tokens: 912 },
			created_at: now,
		};
		list.push(reply);
		portalState.chat[id] = list;
		return HttpResponse.json({ ...reply, message_id: reply.id, conversation_id: "c0" });
	}),
	// ---- post-ingest review ----
	http.get(`${P}/workspaces/:id/review-status`, async ({ request, params }) => {
		await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		const chunks = portalState.chunks[w.id] ?? [];
		return HttpResponse.json({
			workspace_id: w.id,
			kind: w.kind,
			document_version_id: w.reviewed_version_id,
			finalized_version_id: w.finalized_version_id,
			corpus_id: w.corpus_id,
			status: w.status,
			total_chunks: chunks.length,
			approved_chunks: chunks.filter((c) => c.status === "approved").length,
			rejected_chunks: chunks.filter((c) => c.status === "rejected").length,
			draft_chunks: chunks.filter((c) => c.status === "draft").length,
			modified_chunks: 0,
		});
	}),
	http.post(`${P}/workspaces/:id/finalize-review`, async ({ request, params }) => {
		await log(request);
		const w = find(params.id);
		if (!w) return notFound();
		const err = portalState.finalizeError;
		if (err) {
			portalState.finalizeError = null;
			return HttpResponse.json(err.body, { status: err.status });
		}
		w.status = "ingested";
		w.finalized_version_id = "7e500003-0000-4000-8000-000000000003";
		return HttpResponse.json({
			workspace_id: w.id,
			status: "ingested",
			message: "Published",
			document_id: DOC_ID,
			previous_version_id: DOC_VERSION,
			new_version_id: "7e500003-0000-4000-8000-000000000003",
			chunks_in_new_version: 2,
			chunks_unchanged: 1,
			chunks_edited: 1,
			chunks_added: 0,
			parent_chunks: 0,
			staged_chunks_rejected: 0,
			previous_chunks_dropped: 0,
			staged_chunks_total: 2,
			discarded_parent_edits: [
				{
					staged_chunk_id: "c9000001-0000-4000-8000-000000000001",
					parent_chunk_id: "c8000001-0000-4000-8000-000000000001",
					reason: "edit_text",
				},
			],
		});
	}),
	// ---- rules ----
	http.get(`${P}/corpus/:kb/rules`, async ({ request, params }) => {
		await log(request);
		const items = portalState.rules.filter((r) => r.corpus_id === params.kb);
		return HttpResponse.json({ items, count: items.length });
	}),
	http.post(`${P}/corpus/:kb/rules`, async ({ request, params }) => {
		const body = await log(request);
		const rule: Rule = {
			id: `7a0000ff-0000-4000-8000-${String(portalState.rules.length + 1).padStart(12, "0")}`,
			corpus_id: String(params.kb),
			rule_type: String(body.rule_type),
			condition: body.condition as Record<string, unknown>,
			action: String(body.action),
			priority: Number(body.priority),
			enabled: body.enabled !== false,
			created_at: now,
		};
		portalState.rules.push(rule);
		return HttpResponse.json(rule, { status: 201 });
	}),
	http.patch(`${P}/corpus/:kb/rules/:rid`, async ({ request, params }) => {
		const body = await log(request);
		const r = portalState.rules.find((x) => x.id === params.rid);
		if (!r) return HttpResponse.json({ detail: "Rule not found" }, { status: 404 });
		Object.assign(r, body);
		return HttpResponse.json(r);
	}),
	http.delete(`${P}/corpus/:kb/rules/:rid`, async ({ request, params }) => {
		await log(request);
		portalState.rules = portalState.rules.filter((x) => x.id !== params.rid);
		return new HttpResponse(null, { status: 204 });
	}),
];
