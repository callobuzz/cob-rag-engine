import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { authFetch } from "@/lib/api/auth-fetch";
import { api, unwrap } from "@/lib/api/client";
import { ApiError, parseApiError, toApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import { readSse } from "@/lib/api/sse";
import { currentRagUrl } from "@/lib/auth/store";
import { initialTurn, reduceTurn } from "./stream";
import type { CobBlock, CobConversation, CobTurnView } from "./types";

/**
 * COB's data layer (COB2 routes): the chat stream, history, feedback and per-screen starters.
 * Every call goes through the shared authed fetch, so only a session is ever sent (keys are
 * refused by the engine). Query keys live here, under the `cob` root.
 */
export const HISTORY_PAGE = 50;
export const MESSAGE_MAX = 2000;
export const FEEDBACK_COMMENT_MAX = 500;

export const cobKeys = {
	all: ["cob"] as const,
	conversations: ["cob", "conversations"] as const,
	conversation: (id: string) => ["cob", "conversation", id] as const,
	starters: (route: string) => ["cob", "starters", route] as const,
};

export const cobConversationsQuery = queryOptions({
	queryKey: cobKeys.conversations,
	queryFn: () => unwrap(api().GET("/api/v1/cob/conversations", { params: { query: { limit: HISTORY_PAGE } } })),
	staleTime: 0,
	retry: false,
});

export function cobConversationQuery(id: string) {
	return queryOptions({
		queryKey: cobKeys.conversation(id),
		queryFn: () =>
			unwrap(api().GET("/api/v1/cob/conversations/{conversation_id}", { params: { path: { conversation_id: id } } })),
		staleTime: 0,
		retry: false,
	});
}

export function cobStartersQuery(route: string) {
	return queryOptions({
		queryKey: cobKeys.starters(route),
		queryFn: () => unwrap(api().GET("/api/v1/cob/starters", { params: { query: { route } } })),
		staleTime: 5 * 60_000,
		retry: false,
	});
}

export function useDeleteCobConversation() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(
				api().DELETE("/api/v1/cob/conversations/{conversation_id}", { params: { path: { conversation_id: id } } }),
			),
		onSettled: (_d, _e, id) => {
			qc.removeQueries({ queryKey: cobKeys.conversation(id) });
			return qc.invalidateQueries({ queryKey: cobKeys.conversations });
		},
	});
}

export function useCobFeedback() {
	return useMutation({
		mutationFn: (v: { messageId: string; rating: 1 | -1; comment?: string | null }) =>
			unwrap(
				api().POST("/api/v1/cob/messages/{message_id}/feedback", {
					params: { path: { message_id: v.messageId } },
					body: { rating: v.rating, comment: v.comment?.trim() ? v.comment.trim() : null },
				}),
			),
	});
}

// ---- the chat ------------------------------------------------------------------------------

export const CONV_KEY = "cob:conv";

function readConv(): string | null {
	try {
		return window.sessionStorage.getItem(CONV_KEY);
	} catch {
		return null;
	}
}
function writeConv(id: string | null): void {
	try {
		if (id) window.sessionStorage.setItem(CONV_KEY, id);
		else window.sessionStorage.removeItem(CONV_KEY);
	} catch {
		// Storage blocked: the chat then lives for this page only.
	}
}

export interface CobChatContext {
	route: string;
	kbId: string | null;
	docsSlug: string | null;
}

/** A stored conversation as the turns the panel renders (a question and its answer each). */
export function turnsFromConversation(conv: CobConversation): CobTurnView[] {
	const turns: CobTurnView[] = [];
	for (const m of conv.messages) {
		if (m.role === "user") {
			turns.push(initialTurn(m.content));
			const last = turns[turns.length - 1];
			if (last) last.status = "done";
			continue;
		}
		let t = turns[turns.length - 1];
		if (!t || t.messageId) {
			t = { ...initialTurn(""), status: "done" };
			turns.push(t);
		}
		t.text = m.content;
		t.sources = m.sources ?? [];
		t.blocks = (m.ui ?? []) as CobBlock[];
		t.messageId = m.id;
		t.conversationId = conv.id;
		t.rating = m.rating ?? null;
		t.status = "done";
		t.mode = t.blocks.some((b) => b.kind === "cob_search_results") ? "search" : "answer";
	}
	return turns;
}

export interface CobChat {
	turns: CobTurnView[];
	conversationId: string | null;
	busy: boolean;
	send: (message: string, override?: Partial<CobChatContext>) => Promise<void>;
	stop: () => void;
	reset: () => void;
	retry: () => void;
	loadConversation: (id: string) => Promise<void>;
	markRated: (messageId: string, rating: 1 | -1) => void;
}

export function useCobChat(context: CobChatContext): CobChat {
	const qc = useQueryClient();
	const [turns, setTurns] = useState<CobTurnView[]>([]);
	const [conversationId, setConversationId] = useState<string | null>(readConv);
	const [busy, setBusy] = useState(false);
	const ctx = useRef(context);
	ctx.current = context;
	const conv = useRef(conversationId);
	const turnsRef = useRef(turns);
	turnsRef.current = turns;
	const abort = useRef<AbortController | null>(null);
	const alive = useRef(true);

	useEffect(() => {
		alive.current = true;
		return () => {
			alive.current = false;
			abort.current?.abort();
		};
	}, []);

	const setConv = useCallback((id: string | null) => {
		conv.current = id;
		setConversationId(id);
		writeConv(id);
	}, []);

	/** Update the newest turn, which is the one streaming. */
	const patchLast = useCallback((fn: (t: CobTurnView) => CobTurnView) => {
		if (!alive.current) return;
		setTurns((all) => (all.length ? [...all.slice(0, -1), fn(all[all.length - 1] as CobTurnView)] : all));
	}, []);

	const send = useCallback(
		async (message: string, override?: Partial<CobChatContext>) => {
			const text = message.trim();
			if (!text || abort.current) return;
			const controller = new AbortController();
			abort.current = controller;
			setBusy(true);
			setTurns((all) => [...all, { ...initialTurn(text), live: true }]);
			const c = { ...ctx.current, ...override };
			const attempt = async () => {
				const response = await authFetch(`${currentRagUrl()}/api/v1/cob/chat`, {
					method: "POST",
					headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
					body: JSON.stringify({
						message: text,
						...(conv.current ? { conversation_id: conv.current } : {}),
						context: {
							route: c.route,
							...(c.kbId ? { kb_id: c.kbId } : {}),
							...(c.docsSlug ? { docs_slug: c.docsSlug } : {}),
						},
					}),
					signal: controller.signal,
				});
				if (!response.ok) throw await parseApiError(response);
				if (!response.body) throw new ApiError({ status: 0, code: "network", detail: "The reply stream was empty." });
				let ended = false;
				for await (const ev of readSse(response.body, controller.signal)) {
					patchLast((t) => reduceTurn(t, ev));
					if (ev.event === "meta" || ev.event === "done") {
						try {
							const id = (JSON.parse(ev.data) as { conversation_id?: unknown }).conversation_id;
							if (typeof id === "string" && id && alive.current) setConv(id);
						} catch {
							// reduceTurn already ignored the malformed frame
						}
					}
					if (ev.event === "done" || ev.event === "error") ended = true;
				}
				if (!ended && !controller.signal.aborted) {
					patchLast((t) => reduceTurn(t, { event: "error", data: JSON.stringify({ detail: "", code: "network" }) }));
				}
			};
			try {
				try {
					await attempt();
				} catch (e) {
					// The remembered chat is gone or belongs to another scope: ask again as a fresh chat.
					if (!conv.current || toApiError(e).code !== "conversation_not_found") throw e;
					setConv(null);
					await attempt();
				}
			} catch (e) {
				const err = toApiError(e);
				if (err.code !== "aborted") {
					patchLast((t) =>
						reduceTurn(t, {
							event: "error",
							data: JSON.stringify({ detail: err.detail, ...(err.code ? { code: err.code } : {}) }),
						}),
					);
				}
			} finally {
				if (abort.current === controller) abort.current = null;
				if (alive.current) setBusy(false);
				// Stopped mid-stream: keep what arrived as a finished turn without feedback buttons.
				patchLast((t) => (t.status === "streaming" ? { ...t, status: "done", stage: null } : t));
				void qc.invalidateQueries({ queryKey: cobKeys.conversations });
				void qc.invalidateQueries({ queryKey: qk.usage.consumption });
			}
		},
		[patchLast, qc, setConv],
	);

	const stop = useCallback(() => abort.current?.abort(), []);

	const reset = useCallback(() => {
		abort.current?.abort();
		abort.current = null;
		setBusy(false);
		setTurns([]);
		setConv(null);
	}, [setConv]);

	const retry = useCallback(() => {
		const last = turnsRef.current[turnsRef.current.length - 1];
		if (abort.current || !last || last.status !== "error") return;
		setTurns((all) => all.slice(0, -1));
		void send(last.question);
	}, [send]);

	const loadConversation = useCallback(
		async (id: string) => {
			abort.current?.abort();
			abort.current = null;
			setBusy(false);
			const data = await qc.fetchQuery(cobConversationQuery(id));
			if (!alive.current) return;
			setTurns(turnsFromConversation(data));
			setConv(id);
		},
		[qc, setConv],
	);

	const markRated = useCallback((messageId: string, rating: 1 | -1) => {
		setTurns((all) => all.map((t) => (t.messageId === messageId ? { ...t, rating } : t)));
	}, []);

	// A conversation id remembered for this tab but no turns yet: show the stored chat on first open.
	const restored = useRef(false);
	useEffect(() => {
		if (restored.current) return;
		restored.current = true;
		const id = conv.current;
		if (id) {
			loadConversation(id).catch(() => {
				// Deleted or expired meanwhile: start clean.
				if (alive.current) setConv(null);
			});
		}
	}, [loadConversation, setConv]);

	return { turns, conversationId, busy, send, stop, reset, retry, loadConversation, markRated };
}
