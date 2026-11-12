import { randomUUID } from "node:crypto";
import type { World } from "./mock-engine";

/**
 * COB (COB2 routes) for the fake engine: chat as an SSE stream (a docs answer with a source and a
 * link; the word "cap" gives search mode, "sync" the first-start error), history (list, open,
 * delete), feedback and starters per screen. Same behaviour as the unit tests' MSW handlers
 * (src/test/msw/handlers/cob.ts). State is kept per world; nothing is shared with other mocks.
 */
type Json = Record<string, unknown>;

interface Conv {
	id: string;
	title: string;
	created_at: string;
	updated_at: string;
	messages: Json[];
}
export interface CobMockState {
	conversations: Conv[];
	feedback: { id: string; rating: number; comment: string | null }[];
	chats: Json[];
	/** COB5: the action cards that were proposed, by id. */
	proposals: Record<string, { tool: string; confirm_name: string | null; status: string; secret: string | null }>;
}

const states = new WeakMap<World, CobMockState>();
export function cobOf(w: World): CobMockState {
	let s = states.get(w);
	if (!s) {
		s = { conversations: [], feedback: [], chats: [], proposals: {} };
		states.set(w, s);
	}
	return s;
}

const SOURCE = {
	index: 1,
	slug: "screens/widget",
	anchor: "embed-the-widget",
	title: "Website widget: Embed the widget",
	snippet: "Paste the snippet before the closing body tag.",
	route: "/widget",
	public_url: "https://callobuzz.github.io/cob-rag-engine/guide.html",
};
const LINK = { kind: "cob_link", label: "Open Website widget →", route: "/widget", docs_slug: null };
const ANSWER = "Open **Website widget** and copy the snippet [1].";

const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

export interface CobCtx {
	reply: (status: number, payload?: unknown) => unknown;
	/** An SSE body, with the mock's CORS headers. */
	sse: (body: string) => unknown;
}

export async function handleCobRoute(w: World, method: string, url: URL, body: Json, ctx: CobCtx): Promise<boolean> {
	const path = url.pathname;
	if (!path.startsWith("/api/v1/cob/")) return false;
	const st = cobOf(w);

	if (method === "POST" && path === "/api/v1/cob/chat") {
		st.chats.push(body);
		const message = String(body.message ?? "");
		const lower = message.toLowerCase();
		if (lower.includes("sync")) {
			await ctx.sse(frame("error", { detail: "COB is reading the docs.", code: "cob_docs_syncing" }));
			return true;
		}
		// COB5: a request to change something is answered with a card, never with an action.
		const card = actionCard(st, lower);
		if (card) {
			await ctx.sse(streamCard(st, body, message, card));
			return true;
		}
		const search = lower.includes("cap");
		const now = new Date().toISOString();
		let conv = st.conversations.find((c) => c.id === body.conversation_id);
		if (!conv) {
			conv = { id: randomUUID(), title: message.slice(0, 60), created_at: now, updated_at: now, messages: [] };
			st.conversations.unshift(conv);
		}
		const messageId = randomUUID();
		const ui = search ? [{ kind: "cob_search_results", reason: "question_cap_reached", items: [SOURCE] }] : [LINK];
		conv.updated_at = now;
		conv.messages.push(
			{ id: randomUUID(), role: "user", content: message, created_at: now, sources: [], ui: [], rating: null },
			{
				id: messageId,
				role: "assistant",
				content: search ? "" : ANSWER,
				created_at: now,
				sources: search ? [] : [SOURCE],
				ui,
				rating: null,
			},
		);
		const mode = search ? "search" : "answer";
		const events = [
			frame("meta", {
				conversation_id: conv.id,
				query_id: randomUUID(),
				intent: "docs",
				mode,
				language: "en",
				role: "owner",
			}),
			...(search
				? [frame("ui", ui[0])]
				: [
						frame("stage", { stage: "search", elapsed_ms: 40 }),
						frame("sources", { sources: [SOURCE] }),
						frame("token", { content: "Open **Website widget** " }),
						frame("token", { content: "and copy the snippet [1]." }),
						frame("ui", LINK),
					]),
			frame("done", {
				message_id: messageId,
				conversation_id: conv.id,
				intent: "docs",
				mode,
				counted: !search,
				label: "business_question",
				route: "grounded",
				language: "en",
				claims_removed: 0,
				latency_ms: 900,
			}),
		];
		await ctx.sse(events.join(""));
		return true;
	}

	if (method === "GET" && path === "/api/v1/cob/conversations") {
		await ctx.reply(200, {
			items: st.conversations.map((c) => ({
				id: c.id,
				title: c.title,
				created_at: c.created_at,
				updated_at: c.updated_at,
				message_count: c.messages.length,
			})),
			total: st.conversations.length,
		});
		return true;
	}

	const one = /^\/api\/v1\/cob\/conversations\/([^/]+)$/.exec(path);
	if (one) {
		const conv = st.conversations.find((c) => c.id === one[1]);
		if (!conv) {
			await ctx.reply(404, { detail: "That conversation doesn't exist.", code: "conversation_not_found" });
			return true;
		}
		if (method === "GET") {
			await ctx.reply(200, { id: conv.id, title: conv.title, messages: conv.messages });
			return true;
		}
		if (method === "DELETE") {
			st.conversations = st.conversations.filter((c) => c.id !== conv.id);
			await ctx.reply(204);
			return true;
		}
	}

	const fb = /^\/api\/v1\/cob\/messages\/([^/]+)\/feedback$/.exec(path);
	if (fb && method === "POST") {
		st.feedback.push({
			id: fb[1] ?? "",
			rating: Number(body.rating),
			comment: (body.comment as string | null) ?? null,
		});
		await ctx.reply(204);
		return true;
	}

	// COB5
	const act = /^\/api\/v1\/cob\/actions\/([^/]+)\/(confirm|cancel)$/.exec(path);
	if (act && method === "POST") {
		const [status, payload] = actionResult(st, act[1] ?? "", act[2] ?? "", body);
		await ctx.reply(status, payload);
		return true;
	}
	// end COB5

	if (method === "GET" && path === "/api/v1/cob/starters") {
		const route = url.searchParams.get("route") ?? "/";
		await ctx.reply(200, {
			route,
			page_slug: route === "/widget" ? "screens/widget" : null,
			starters:
				route === "/widget"
					? ["How do I put the chat on my website?", "Why does the widget say not allowed on this site?"]
					: ["What can I do on this screen?", "Where do I start?"],
		});
		return true;
	}
	return false;
}

// COB5 -------------------------------------------------------------------------------------------

interface Card extends Json {
	proposal_id: string;
}

/**
 * The card a request asks for: "widget key" (low), "rotate" (medium, secret shown once),
 * "delete ... knowledge base" (high, typed name) and "expired" (already past its time).
 */
function actionCard(st: CobMockState, lower: string): Card | null {
	const id = randomUUID();
	const expires = new Date(Date.now() + 10 * 60_000).toISOString();
	const base = { kind: "cob_action_card", proposal_id: id, status: "pending", expires_at: expires };
	let card: Card | null = null;
	if (lower.includes("expired")) {
		card = {
			...base,
			tool: "create_widget_key",
			title: "Create a widget key",
			summary: "Create the widget key **Main site** for **Shree Labs**.",
			args: [{ label: "Website", value: "https://shreelabs.in" }],
			risk: "low",
			destructive: false,
			confirm_name: null,
			expires_at: new Date(Date.now() - 1000).toISOString(),
		};
	} else if (lower.includes("widget key")) {
		card = {
			...base,
			tool: "create_widget_key",
			title: "Create a widget key",
			summary: "Create the widget key **Main site** for **Shree Labs**, allowed on https://shreelabs.in.",
			args: [
				{ label: "Knowledge base", value: "Shree Labs" },
				{ label: "Website", value: "https://shreelabs.in" },
			],
			risk: "low",
			destructive: false,
			confirm_name: null,
		};
	} else if (lower.includes("rotate")) {
		card = {
			...base,
			tool: "rotate_key",
			title: "Rotate a key",
			summary: "Replace the secret of the key **Zapier** (Workspace). The old key stops working at once.",
			args: [{ label: "Key", value: "Zapier" }],
			risk: "medium",
			destructive: false,
			confirm_name: null,
		};
	} else if (lower.includes("delete") && lower.includes("knowledge base")) {
		card = {
			...base,
			tool: "delete_knowledge_base",
			title: "Delete a knowledge base",
			summary: "Delete **Shree Labs** with its 12 documents. This cannot be undone. Type its name to confirm.",
			args: [{ label: "Knowledge base", value: "Shree Labs" }],
			risk: "high",
			destructive: true,
			confirm_name: "Shree Labs",
		};
	}
	if (card) {
		st.proposals[id] = {
			tool: String(card.tool),
			confirm_name: (card.confirm_name as string | null) ?? null,
			status: "pending",
			secret: card.tool === "rotate_key" ? "tk_E2E_SECRET_123" : null,
		};
	}
	return card;
}

function streamCard(st: CobMockState, body: Json, message: string, card: Card): string {
	const now = new Date().toISOString();
	let conv = st.conversations.find((c) => c.id === body.conversation_id);
	if (!conv) {
		conv = { id: randomUUID(), title: message.slice(0, 60), created_at: now, updated_at: now, messages: [] };
		st.conversations.unshift(conv);
	}
	const messageId = randomUUID();
	const intro = card.destructive
		? "This cannot be undone. Check the card and type the name to confirm."
		: "I can do that. Check the card and confirm.";
	conv.updated_at = now;
	conv.messages.push(
		{ id: randomUUID(), role: "user", content: message, created_at: now, sources: [], ui: [], rating: null },
		{ id: messageId, role: "assistant", content: intro, created_at: now, sources: [], ui: [card], rating: null },
	);
	return [
		frame("meta", {
			conversation_id: conv.id,
			query_id: randomUUID(),
			intent: "action",
			mode: "answer",
			language: "en",
			role: "owner",
		}),
		frame("stage", { stage: "propose", elapsed_ms: 20 }),
		frame("token", { content: intro }),
		frame("ui", card),
		frame("done", {
			message_id: messageId,
			conversation_id: conv.id,
			intent: "action",
			mode: "answer",
			counted: false,
			label: null,
			route: "action",
			language: "en",
			claims_removed: 0,
			latency_ms: 40,
		}),
	].join("");
}

function actionResult(st: CobMockState, id: string, verb: string, body: Json): [number, unknown] {
	const p = st.proposals[id];
	if (!p) return [404, { detail: "I can't find that card.", code: "cob_proposal_not_found" }];
	if (p.status !== "pending") return [409, { detail: "This card was already used.", code: "cob_proposal_used" }];
	if (verb === "cancel") {
		p.status = "cancelled";
		return [200, { proposal_id: id, status: "cancelled" }];
	}
	if (p.confirm_name != null && String(body.confirm_name ?? "").trim() !== p.confirm_name) {
		return [422, { detail: "The name you typed doesn't match.", code: "confirm_mismatch" }];
	}
	p.status = "done";
	const message =
		p.tool === "delete_knowledge_base"
			? "Knowledge base **Shree Labs** deleted."
			: p.tool === "rotate_key"
				? "Key **Zapier** rotated. Copy the new key now; it is shown once."
				: "Widget key **Main site** created for **Shree Labs**.";
	return [
		200,
		{
			proposal_id: id,
			status: "done",
			message,
			link:
				p.tool === "create_widget_key"
					? { kind: "cob_link", label: "Open Website widget →", route: "/widget", docs_slug: null }
					: null,
			secret: p.secret,
			http_status: p.tool === "create_widget_key" ? 201 : 200,
			code: null,
		},
	];
}

// end COB5
