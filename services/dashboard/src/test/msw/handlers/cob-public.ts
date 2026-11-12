import { HttpResponse, http } from "msw";
import { BASE } from "../data";
import { type SseEvent, sseResponse } from "./query";

/**
 * The public COB (COB6): `POST /api/v1/public/cob/chat` as a scripted SSE stream per message
 * keyword ("limit" -> 429 with Retry-After, "daily" -> the daily 429, "down" -> 503, "sync" ->
 * cob_docs_syncing, "search" -> search mode, default -> a public answer with a source and a link
 * to a public screen). Every request body is kept in `publicCobState.chats`.
 */
export const PUBLIC_SOURCE = {
	index: 1,
	slug: "guide/overview",
	anchor: "",
	title: "What is cob-rag-engine?",
	snippet: "A self-hosted engine for chatbots over your own data.",
	route: null as string | null,
	public_url: "https://callobuzz.github.io/cob-rag-engine/guide.html" as string | null,
};
export const PUBLIC_ANSWER_TOKENS = ["It is a self-hosted engine ", "for chatbots over your data [1]."];

export const publicCobState = {
	chats: [] as { message: string; history?: { role: string; content: string }[] }[],
};

export function resetPublicCobState() {
	publicCobState.chats = [];
}

const meta = (mode: "answer" | "search"): SseEvent => [
	"meta",
	{
		conversation_id: "00000000-0000-4000-8000-000000000000",
		query_id: "q",
		intent: "docs",
		mode,
		language: "en",
		role: "public",
	},
];
const done = (mode: "answer" | "search"): SseEvent => [
	"done",
	{
		message_id: null,
		conversation_id: "00000000-0000-4000-8000-000000000000",
		intent: "docs",
		mode,
		counted: false,
		label: "business_question",
		route: "grounded",
		language: "en",
		claims_removed: 0,
		latency_ms: 800,
	},
];

export const publicCobHandlers = [
	http.post(`${BASE}/api/v1/public/cob/chat`, async ({ request }) => {
		const body = (await request.json()) as { message: string; history?: { role: string; content: string }[] };
		publicCobState.chats.push(body);
		const m = body.message.toLowerCase();
		if (m.includes("daily"))
			return HttpResponse.json(
				{ detail: "x", code: "cob_public_rate_limited", scope: "daily" },
				{ status: 429, headers: { "Retry-After": "7200" } },
			);
		if (m.includes("limit"))
			return HttpResponse.json(
				{ detail: "x", code: "cob_public_rate_limited", scope: "ip" },
				{ status: 429, headers: { "Retry-After": "600" } },
			);
		if (m.includes("down"))
			return HttpResponse.json(
				{ detail: "COB is unavailable right now.", code: "cob_public_unavailable" },
				{ status: 503 },
			);
		if (m.includes("sync"))
			return sseResponse([["error", { detail: "COB is reading the docs.", code: "cob_docs_syncing" }]]);
		if (m.includes("search")) {
			return sseResponse([
				meta("search"),
				["ui", { kind: "cob_search_results", reason: "llm_unavailable", items: [PUBLIC_SOURCE] }],
				done("search"),
			]);
		}
		return sseResponse([
			meta("answer"),
			["stage", { stage: "search", elapsed_ms: 30 }],
			["sources", { sources: [PUBLIC_SOURCE] }],
			...PUBLIC_ANSWER_TOKENS.map((c): SseEvent => ["token", { content: c }]),
			["ui", { kind: "cob_link", label: "Open Log in →", route: "/login", docs_slug: null }],
			done("answer"),
		]);
	}),
];
