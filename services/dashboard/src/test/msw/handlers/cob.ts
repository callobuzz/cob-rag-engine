import { HttpResponse, http } from "msw";
import { BASE } from "../data";
import { type SseEvent, sseResponse } from "./query";

/**
 * COB (COB2 routes): chat as a scripted SSE stream per message keyword ("cap" -> search mode,
 * "sync" -> cob_docs_syncing, "boom" -> a failed request, default -> a docs answer with a source
 * and a link), history, feedback and starters per screen. Stateful per test; `resetCobState()`.
 */
export const COB_CONV = "c0b00000-0000-4000-8000-000000000001";
export const COB_MSG = "c0b00000-0000-4000-8000-0000000000a1";

export const COB_SOURCE = {
	index: 1,
	slug: "screens/widget",
	anchor: "embed-the-widget",
	title: "Website widget — Embed the widget",
	snippet: "Paste the snippet before </body>.",
	route: "/widget",
	public_url: null as string | null,
};

export const COB_ANSWER_TOKENS = ["Open **Website widget** ", "and copy the snippet [1]."];

interface Conv {
	id: string;
	title: string | null;
	created_at: string;
	updated_at: string;
	messages: Record<string, unknown>[];
}

const seed = (): Conv[] => [
	{
		id: COB_CONV,
		title: "Embedding the widget",
		created_at: "2026-10-03T09:00:00Z",
		updated_at: "2026-10-03T09:02:00Z",
		messages: [
			{
				id: "c0b00000-0000-4000-8000-0000000000a0",
				role: "user",
				content: "How do I embed the widget?",
				created_at: "2026-10-03T09:00:00Z",
				sources: [],
				ui: [],
				rating: null,
			},
			{
				id: COB_MSG,
				role: "assistant",
				content: "Open **Website widget** and copy the snippet [1].",
				created_at: "2026-10-03T09:00:05Z",
				sources: [COB_SOURCE],
				ui: [{ kind: "cob_link", label: "Open Website widget →", route: "/widget", docs_slug: null }],
				rating: null,
			},
		],
	},
];

export const cobState = {
	conversations: seed(),
	chats: [] as { message: string; conversation_id?: string; context?: Record<string, unknown> }[],
	feedback: [] as { id: string; rating: number; comment: string | null }[],
};

export function resetCobState() {
	cobState.conversations = seed();
	cobState.chats = [];
	cobState.feedback = [];
}

const meta = (mode: "answer" | "search" = "answer"): SseEvent => [
	"meta",
	{ conversation_id: COB_CONV, query_id: "q1", intent: "docs", mode, language: "en", role: "owner" },
];

function doneEvent(mode: "answer" | "search", counted: boolean): SseEvent {
	return [
		"done",
		{
			message_id: COB_MSG,
			conversation_id: COB_CONV,
			intent: "docs",
			mode,
			counted,
			label: "business_question",
			route: "grounded",
			language: "en",
			claims_removed: 0,
			latency_ms: 900,
		},
	];
}

export function scriptFor(message: string): SseEvent[] {
	const m = message.toLowerCase();
	if (m.includes("sync")) return [["error", { detail: "COB is reading the docs.", code: "cob_docs_syncing" }]];
	if (m.includes("cap")) {
		return [
			meta("search"),
			[
				"ui",
				{
					kind: "cob_search_results",
					reason: "question_cap_reached",
					items: [COB_SOURCE],
				},
			],
			doneEvent("search", false),
		];
	}
	return [
		meta(),
		["stage", { stage: "search", elapsed_ms: 40 }],
		["sources", { sources: [COB_SOURCE] }],
		["stage", { stage: "write", elapsed_ms: 300 }],
		...COB_ANSWER_TOKENS.map((c): SseEvent => ["token", { content: c }]),
		["ui", { kind: "cob_link", label: "Open Website widget →", route: "/widget", docs_slug: null }],
		doneEvent("answer", true),
	];
}

export const cobHandlers = [
	http.post(`${BASE}/api/v1/cob/chat`, async ({ request }) => {
		const body = (await request.json()) as {
			message: string;
			conversation_id?: string;
			context?: Record<string, unknown>;
		};
		cobState.chats.push(body);
		if (body.message.toLowerCase().includes("boom")) {
			return HttpResponse.json({ detail: "COB couldn't answer just now.", code: "internal_error" }, { status: 500 });
		}
		return sseResponse(scriptFor(body.message));
	}),
	http.get(`${BASE}/api/v1/cob/conversations`, () =>
		HttpResponse.json({
			items: cobState.conversations.map((c) => ({
				id: c.id,
				title: c.title,
				created_at: c.created_at,
				updated_at: c.updated_at,
				message_count: c.messages.length,
			})),
			total: cobState.conversations.length,
		}),
	),
	http.get(`${BASE}/api/v1/cob/conversations/:id`, ({ params }) => {
		const c = cobState.conversations.find((x) => x.id === params.id);
		if (!c)
			return HttpResponse.json(
				{ detail: "That conversation doesn't exist.", code: "conversation_not_found" },
				{ status: 404 },
			);
		return HttpResponse.json({ id: c.id, title: c.title, messages: c.messages });
	}),
	http.delete(`${BASE}/api/v1/cob/conversations/:id`, ({ params }) => {
		cobState.conversations = cobState.conversations.filter((x) => x.id !== params.id);
		return new HttpResponse(null, { status: 204 });
	}),
	http.post(`${BASE}/api/v1/cob/messages/:id/feedback`, async ({ params, request }) => {
		const b = (await request.json()) as { rating: number; comment: string | null };
		cobState.feedback.push({ id: String(params.id), rating: b.rating, comment: b.comment ?? null });
		return new HttpResponse(null, { status: 204 });
	}),
	http.get(`${BASE}/api/v1/cob/starters`, ({ request }) => {
		const route = new URL(request.url).searchParams.get("route") ?? "/";
		const starters =
			route === "/widget"
				? ["How do I put the chat on my website?", "Why does the widget say not allowed on this site?"]
				: ["What can I do on this screen?", "Where do I start?"];
		return HttpResponse.json({ route, page_slug: route === "/widget" ? "screens/widget" : null, starters });
	}),
];
