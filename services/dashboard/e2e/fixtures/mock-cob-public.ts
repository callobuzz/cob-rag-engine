import type { World } from "./mock-engine";

/**
 * The public COB (COB6) for the fake engine: `POST /api/v1/public/cob/chat` as an SSE stream, no
 * credential. A docs answer with a public source and a link to the sign-in page; the word "limit"
 * gives the per-visitor 429 with Retry-After. Same behaviour as the unit tests' MSW handlers
 * (src/test/msw/handlers/cob-public.ts). The requests are kept per world for assertions.
 */
type Json = Record<string, unknown>;

export const publicChats = new WeakMap<World, Json[]>();

const SOURCE = {
	index: 1,
	slug: "guide/overview",
	anchor: "",
	title: "What is cob-rag-engine?",
	snippet: "A self-hosted engine for chatbots over your own data.",
	route: null,
	public_url: "https://callobuzz.github.io/cob-rag-engine/guide.html",
};
const LINK = { kind: "cob_link", label: "Open Log in →", route: "/login", docs_slug: null };
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

export interface CobPublicCtx {
	reply: (status: number, payload: unknown, headers?: Record<string, string>) => unknown;
	sse: (body: string) => unknown;
}

export async function handleCobPublicRoute(
	w: World,
	method: string,
	path: string,
	body: Json,
	ctx: CobPublicCtx,
): Promise<boolean> {
	if (method !== "POST" || path !== "/api/v1/public/cob/chat") return false;
	const chats = publicChats.get(w) ?? [];
	publicChats.set(w, chats);
	chats.push(body);
	if (w.branding.cob_public === false) {
		await ctx.reply(404, { detail: "Ask COB is switched off on this engine.", code: "cob_public_disabled" });
		return true;
	}
	if (
		String(body.message ?? "")
			.toLowerCase()
			.includes("limit")
	) {
		await ctx.reply(429, { detail: "x", code: "cob_public_rate_limited", scope: "ip" }, { "retry-after": "600" });
		return true;
	}
	const done = {
		message_id: null,
		conversation_id: "00000000-0000-4000-8000-000000000000",
		intent: "docs",
		mode: "answer",
		counted: false,
		label: "business_question",
		route: "grounded",
		language: "en",
		claims_removed: 0,
		latency_ms: 800,
	};
	await ctx.sse(
		[
			frame("meta", {
				conversation_id: done.conversation_id,
				query_id: "q",
				intent: "docs",
				mode: "answer",
				language: "en",
				role: "public",
			}),
			frame("stage", { stage: "search", elapsed_ms: 40 }),
			frame("sources", { sources: [SOURCE] }),
			frame("token", { content: "It is a self-hosted engine " }),
			frame("token", { content: "for chatbots over your data [1]." }),
			frame("ui", LINK),
			frame("done", done),
		].join(""),
	);
	return true;
}
