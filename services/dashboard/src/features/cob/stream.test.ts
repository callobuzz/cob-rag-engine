import { describe, expect, it } from "vitest";
import { initialTurn, reduceTurn } from "./stream";

const ev = (event: string, data: unknown) => ({ event, data: JSON.stringify(data) });

describe("reduceTurn", () => {
	it("accumulates tokens, keeps sources, link and done", () => {
		let t = initialTurn("How do I embed the widget?");
		for (const e of [
			ev("meta", {
				conversation_id: "c1",
				query_id: "q1",
				intent: "docs",
				mode: "answer",
				language: "en",
				role: "owner",
			}),
			ev("stage", { stage: "search", elapsed_ms: 40 }),
			ev("sources", {
				sources: [
					{
						index: 1,
						slug: "screens/widget",
						anchor: "embed-the-widget",
						title: "Website widget — Embed",
						snippet: "Paste…",
						route: "/widget",
						public_url: null,
					},
				],
			}),
			ev("token", { content: "Open **Website widget** " }),
			ev("token", { content: "and copy the snippet [1]." }),
			ev("ui", { kind: "cob_link", label: "Open Website widget →", route: "/widget", docs_slug: null }),
			ev("done", {
				message_id: "m1",
				conversation_id: "c1",
				intent: "docs",
				mode: "answer",
				counted: true,
				label: "business_question",
				route: "grounded",
				language: "en",
				claims_removed: 0,
				latency_ms: 900,
			}),
		])
			t = reduceTurn(t, e);
		expect(t.text).toBe("Open **Website widget** and copy the snippet [1].");
		expect(t.sources).toHaveLength(1);
		expect(t.blocks).toEqual([{ kind: "cob_link", label: "Open Website widget →", route: "/widget", docs_slug: null }]);
		expect(t.status).toBe("done");
		expect(t.messageId).toBe("m1");
		expect(t.conversationId).toBe("c1");
		expect(t.counted).toBe(true);
		expect(t.stage).toBeNull();
	});

	it("search mode keeps the results block and marks not counted", () => {
		let t = initialTurn("pricing?");
		t = reduceTurn(t, ev("meta", { conversation_id: "c2", mode: "search", intent: "docs" }));
		t = reduceTurn(
			t,
			ev("ui", {
				kind: "cob_search_results",
				reason: "question_cap_reached",
				items: [{ index: 1, slug: "screens/plan", anchor: "", title: "Plan", snippet: "x", route: "/plan" }],
			}),
		);
		t = reduceTurn(t, ev("done", { message_id: "m2", conversation_id: "c2", mode: "search", counted: false }));
		expect(t.mode).toBe("search");
		expect(t.counted).toBe(false);
		expect(t.blocks[0]?.kind).toBe("cob_search_results");
		expect(t.status).toBe("done");
	});

	it("tracks the stage and clears it on the first token", () => {
		let t = initialTurn("x");
		t = reduceTurn(t, ev("stage", { stage: "tool", elapsed_ms: 10 }));
		expect(t.stage).toBe("tool");
		t = reduceTurn(t, ev("token", { content: "Hi" }));
		expect(t.stage).toBeNull();
	});

	it("keeps quick replies", () => {
		const t = reduceTurn(initialTurn("x"), ev("quick_replies", { items: [{ label: "Yes" }] }));
		expect(t.quickReplies).toEqual([{ label: "Yes" }]);
	});

	it("error replaces streaming state and keeps the code", () => {
		const t = reduceTurn(
			initialTurn("x"),
			ev("error", { detail: "COB is reading the docs.", code: "cob_docs_syncing" }),
		);
		expect(t.status).toBe("error");
		expect(t.error?.code).toBe("cob_docs_syncing");
		expect(t.error?.detail).toBe("COB is reading the docs.");
	});

	it("ignores unknown events and malformed JSON", () => {
		let t = initialTurn("x");
		t = reduceTurn(t, ev("weird", { a: 1 }));
		t = reduceTurn(t, { event: "token", data: "{not json" });
		t = reduceTurn(t, { event: "token", data: "[1]" });
		expect(t.status).toBe("streaming");
		expect(t.text).toBe("");
	});

	it("keeps unknown ui kinds as they are", () => {
		const t = reduceTurn(initialTurn("x"), ev("ui", { kind: "cob_action_card", proposal_id: "p1" }));
		expect(t.blocks).toEqual([{ kind: "cob_action_card", proposal_id: "p1" }]);
	});
});
