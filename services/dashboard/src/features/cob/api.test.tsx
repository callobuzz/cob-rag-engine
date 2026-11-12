import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { BASE } from "@/test/msw/data";
import { COB_CONV, COB_MSG, cobHandlers, cobState, resetCobState, scriptFor } from "@/test/msw/handlers/cob";
import { sseResponse } from "@/test/msw/handlers/query";
import { server } from "@/test/msw/server";
import { cobStartersQuery, turnsFromConversation, useCobChat, useCobFeedback } from "./api";

beforeEach(() => {
	resetCobState();
	sessionStorage.clear();
	server.use(...cobHandlers);
});

function wrapper() {
	const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
	return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const CTX = { route: "/widget", kbId: "kb-1", docsSlug: null };

describe("useCobChat", () => {
	it("send streams a turn and remembers the conversation", async () => {
		const { result } = renderHook(() => useCobChat(CTX), { wrapper: wrapper() });
		await act(() => result.current.send("How do I embed the widget?"));
		await waitFor(() => expect(result.current.turns[0]?.status).toBe("done"));
		const t = result.current.turns[0];
		expect(t?.text).toBe("Open **Website widget** and copy the snippet [1].");
		expect(t?.sources).toHaveLength(1);
		expect(t?.messageId).toBe(COB_MSG);
		expect(result.current.conversationId).toBe(COB_CONV);
		expect(sessionStorage.getItem("cob:conv")).toBe(COB_CONV);
		expect(cobState.chats[0]).toMatchObject({
			message: "How do I embed the widget?",
			context: { route: "/widget", kb_id: "kb-1" },
		});
	});

	it("the next send continues the conversation; reset starts clean", async () => {
		const { result } = renderHook(() => useCobChat(CTX), { wrapper: wrapper() });
		await act(() => result.current.send("one"));
		await act(() => result.current.send("two"));
		expect(cobState.chats[1]?.conversation_id).toBe(COB_CONV);
		act(() => result.current.reset());
		expect(result.current.turns).toEqual([]);
		expect(sessionStorage.getItem("cob:conv")).toBeNull();
	});

	it("a failed request becomes an error turn that retry replaces", async () => {
		const { result } = renderHook(() => useCobChat(CTX), { wrapper: wrapper() });
		await act(() => result.current.send("boom"));
		expect(result.current.turns[0]?.status).toBe("error");
		expect(result.current.turns[0]?.error?.code).toBe("internal_error");
	});

	it("a conversation the engine no longer knows is dropped and the question goes out as a fresh chat", async () => {
		const { result } = renderHook(() => useCobChat(CTX), { wrapper: wrapper() });
		await act(() => result.current.send("one"));
		expect(result.current.conversationId).toBe(COB_CONV);
		const seen: (string | undefined)[] = [];
		server.use(
			http.post(`${BASE}/api/v1/cob/chat`, async ({ request }) => {
				const body = (await request.json()) as { message: string; conversation_id?: string };
				seen.push(body.conversation_id);
				if (body.conversation_id) {
					return HttpResponse.json({ detail: "That chat is gone.", code: "conversation_not_found" }, { status: 404 });
				}
				return sseResponse(scriptFor(body.message));
			}),
		);
		await act(() => result.current.send("two"));
		expect(seen).toEqual([COB_CONV, undefined]);
		expect(result.current.turns[1]?.status).toBe("done");
		expect(result.current.turns[1]?.text).toContain("Website widget");
	});

	it("stop aborts the stream and keeps what arrived", async () => {
		const { result } = renderHook(() => useCobChat(CTX), { wrapper: wrapper() });
		let p!: Promise<void>;
		act(() => {
			p = result.current.send("hello");
			result.current.stop();
		});
		await act(() => p);
		expect(result.current.busy).toBe(false);
		expect(result.current.turns[0]?.status).not.toBe("streaming");
	});

	it("loadConversation shows the stored question and answer", async () => {
		const { result } = renderHook(() => useCobChat(CTX), { wrapper: wrapper() });
		await act(() => result.current.loadConversation(COB_CONV));
		expect(result.current.turns).toHaveLength(1);
		expect(result.current.turns[0]?.question).toBe("How do I embed the widget?");
		expect(result.current.turns[0]?.blocks[0]?.kind).toBe("cob_link");
		expect(result.current.conversationId).toBe(COB_CONV);
	});
});

describe("feedback and starters", () => {
	it("feedback posts the rating and the trimmed comment", async () => {
		const { result } = renderHook(() => useCobFeedback(), { wrapper: wrapper() });
		await act(() => result.current.mutateAsync({ messageId: COB_MSG, rating: -1, comment: "  wrong page " }));
		expect(cobState.feedback).toEqual([{ id: COB_MSG, rating: -1, comment: "wrong page" }]);
		await act(() => result.current.mutateAsync({ messageId: COB_MSG, rating: 1 }));
		expect(cobState.feedback[1]).toEqual({ id: COB_MSG, rating: 1, comment: null });
	});

	it("starters are keyed by route", () => {
		expect(cobStartersQuery("/widget").queryKey).not.toEqual(cobStartersQuery("/usage").queryKey);
	});

	it("turnsFromConversation pairs messages and keeps ratings", () => {
		const turns = turnsFromConversation({
			id: "c",
			title: null,
			messages: [
				{ id: "u1", role: "user", content: "q", created_at: "2026-10-03T00:00:00Z" },
				{ id: "a1", role: "assistant", content: "a", created_at: "2026-10-03T00:00:01Z", rating: -1 },
			],
		});
		expect(turns).toHaveLength(1);
		expect(turns[0]).toMatchObject({ question: "q", text: "a", messageId: "a1", rating: -1, status: "done" });
	});
});
