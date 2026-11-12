import { HttpResponse, http } from "msw";
import { BASE, CORPORA } from "../data";
import { type SseEvent, sseResponse } from "./query";

/**
 * Conversation handlers (D5), stateful per test: list, create, rename, delete, messages
 * (oldest first, offset paging) and send — JSON, or SSE when `Accept: text/event-stream`
 * (api/services/streaming_service.py: thinking, token, done, error). Indian demo data.
 */
const KB = CORPORA[0]?.id ?? "";

interface Conv {
	id: string;
	corpus_id: string;
	title: string | null;
	message_count: number;
	total_tokens: number;
	created_at: string;
	updated_at: string;
}
interface Msg {
	id: string;
	conversation_id: string;
	role: "user" | "assistant";
	content: string;
	token_count: number;
	created_at: string;
	retrieval_context: Record<string, unknown> | null;
}

export const CONV_REPORTS = "c0de0000-0000-4000-8000-000000000001";
export const CONV_SLOTS = "c0de0000-0000-4000-8000-000000000002";

const seedConvs = (): Conv[] => [
	{
		id: CONV_REPORTS,
		corpus_id: KB,
		title: "Report timings",
		message_count: 2,
		total_tokens: 420,
		created_at: "2026-09-29T09:00:00Z",
		updated_at: "2026-09-29T09:05:00Z",
	},
	{
		id: CONV_SLOTS,
		corpus_id: KB,
		title: null,
		message_count: 0,
		total_tokens: 0,
		created_at: "2026-09-28T09:00:00Z",
		updated_at: "2026-09-28T09:00:00Z",
	},
];
const seedMsgs = (): Msg[] => [
	{
		id: "m0000000-0000-4000-8000-000000000001",
		conversation_id: CONV_REPORTS,
		role: "user",
		content: "रिपोर्ट कब मिलेगी?",
		token_count: 0,
		created_at: "2026-09-29T09:00:00Z",
		retrieval_context: null,
	},
	{
		id: "m0000000-0000-4000-8000-000000000002",
		conversation_id: CONV_REPORTS,
		role: "assistant",
		content: "ज़्यादातर रिपोर्ट उसी दिन शाम 7 बजे तक मिल जाती हैं।",
		token_count: 420,
		created_at: "2026-09-29T09:00:05Z",
		retrieval_context: {
			chunks: [
				{
					chunk_id: "c0000000-0000-4000-8000-000000000009",
					text: "Most reports are ready the same day by 7 pm.",
					score: 0.812,
					document_id: "d0c00000-0000-4000-8000-000000000003",
				},
			],
		},
	},
];

export const conversationsState = { convs: seedConvs(), msgs: seedMsgs(), n: 10 };
export function resetConversationsState(): void {
	conversationsState.convs = seedConvs();
	conversationsState.msgs = seedMsgs();
	conversationsState.n = 10;
}

const newId = () => `c0de0000-0000-4000-8000-${String(++conversationsState.n).padStart(12, "0")}`;
const notFound = () => HttpResponse.json({ detail: "Conversation not found" }, { status: 404 });

export const REPLY_EVENTS: SseEvent[] = [
	["stage", { stage: "understand", elapsed_ms: 0 }],
	["thinking", { step: "Analyzing query..." }],
	["stage", { stage: "search", elapsed_ms: 40 }],
	["thinking", { step: "Searching knowledge base..." }],
	["tool_call", { tool: "search_kb", params: { query: "Sunday home collection Baner" } }],
	["tool_result", { tool: "search_kb", summary: "3 passages", duration_ms: 84 }],
	["stage", { stage: "write", elapsed_ms: 120 }],
	["thinking", { step: "Generating response..." }],
	["token", { content: "हाँ, रविवार को भी " }],
	["token", { content: "Baner में घर से सैंपल लिया जाता है।" }],
	[
		"done",
		{
			message_id: "m-new",
			total_tokens: 96,
			latency_ms: 900,
			timings: { understand: 40, search: 80, write: 780 },
		},
	],
];

export const conversationsHandlers = [
	http.get(`${BASE}/api/v1/corpora/:kb/conversations`, ({ params, request }) => {
		const u = new URL(request.url);
		const limit = Number(u.searchParams.get("limit") ?? 50);
		const offset = Number(u.searchParams.get("offset") ?? 0);
		const all = conversationsState.convs.filter((c) => c.corpus_id === params.kb);
		return HttpResponse.json({ items: all.slice(offset, offset + limit), count: all.length, offset, limit });
	}),
	http.post(`${BASE}/api/v1/corpora/:kb/conversations`, async ({ params, request }) => {
		const body = (await request.json()) as { title?: string | null };
		const now = new Date().toISOString();
		const c: Conv = {
			id: newId(),
			corpus_id: String(params.kb),
			title: body.title ?? null,
			message_count: 0,
			total_tokens: 0,
			created_at: now,
			updated_at: now,
		};
		conversationsState.convs.unshift(c);
		return HttpResponse.json(c, { status: 201 });
	}),
	http.patch(`${BASE}/api/v1/corpora/:kb/conversations/:id`, async ({ params, request }) => {
		const c = conversationsState.convs.find((x) => x.id === params.id);
		if (!c) return notFound();
		const body = (await request.json()) as { title?: string };
		if (body.title != null) c.title = body.title;
		return HttpResponse.json(c);
	}),
	http.delete(`${BASE}/api/v1/corpora/:kb/conversations/:id`, ({ params }) => {
		if (!conversationsState.convs.some((x) => x.id === params.id)) return notFound();
		conversationsState.convs = conversationsState.convs.filter((x) => x.id !== params.id);
		return new HttpResponse(null, { status: 204 });
	}),
	http.get(`${BASE}/api/v1/corpora/:kb/conversations/:id/messages`, ({ params, request }) => {
		if (!conversationsState.convs.some((x) => x.id === params.id)) return notFound();
		const u = new URL(request.url);
		const limit = Number(u.searchParams.get("limit") ?? 50);
		const offset = Number(u.searchParams.get("offset") ?? 0);
		const all = conversationsState.msgs.filter((m) => m.conversation_id === params.id);
		return HttpResponse.json({ items: all.slice(offset, offset + limit), count: all.length, offset, limit });
	}),
	http.post(`${BASE}/api/v1/corpora/:kb/conversations/:id/messages`, async ({ params, request }) => {
		const c = conversationsState.convs.find((x) => x.id === params.id);
		if (!c) return notFound();
		const body = (await request.json()) as { content: string };
		const now = Date.now();
		const user: Msg = {
			id: newId(),
			conversation_id: c.id,
			role: "user",
			content: body.content,
			token_count: 0,
			created_at: new Date(now).toISOString(),
			retrieval_context: null,
		};
		const reply: Msg = {
			id: newId(),
			conversation_id: c.id,
			role: "assistant",
			content: "हाँ, रविवार को भी Baner में घर से सैंपल लिया जाता है।",
			token_count: 96,
			created_at: new Date(now + 1000).toISOString(),
			retrieval_context: null,
		};
		conversationsState.msgs.push(user, reply);
		c.message_count += 2;
		c.total_tokens += 96;
		if ((request.headers.get("accept") ?? "").includes("text/event-stream")) return sseResponse(REPLY_EVENTS);
		return HttpResponse.json(
			{ user_message: user, assistant_message: reply, retrieval_context: null },
			{ status: 201 },
		);
	}),
];
