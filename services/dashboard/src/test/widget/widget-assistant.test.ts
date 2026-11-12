// The embeddable widget with the conversation layer on, run for real against widget.js in jsdom:
// the chat-session token from `meta` is remembered and sent back, the page and campaign travel
// with each message, the AI disclosure shows under the title, and the director's extra stage
// names read as plain words. The script is served by the engine with no bundler, so the test
// evaluates its source.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadWidgetScripts } from "./load-widget";

const enc = new TextEncoder();
const KEY = "pk_testkey000000000000";
const SESSION_STORE = "cobrag:pk_testkey000000:session";
const TOKEN = `ws_${"a".repeat(43)}`;

function sse(event: string, data: unknown): Uint8Array {
	return enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

/** A chat response whose body the test feeds by hand. */
function controlledStream() {
	const waiting: Array<(r: { done: boolean; value?: Uint8Array }) => void> = [];
	const queued: Uint8Array[] = [];
	let closed = false;
	return {
		push(chunk: Uint8Array) {
			const w = waiting.shift();
			if (w) w({ done: false, value: chunk });
			else queued.push(chunk);
		},
		close() {
			closed = true;
			for (const w of waiting.splice(0)) w({ done: true });
		},
		response: {
			ok: true,
			status: 200,
			body: {
				getReader: () => ({
					read: () =>
						new Promise((resolve) => {
							const next = queued.shift();
							if (next) resolve({ done: false, value: next });
							else if (closed) resolve({ done: true });
							else waiting.push(resolve);
						}),
				}),
			},
		},
	};
}

const shadow = () => document.querySelector("[data-cobrag-widget]")?.shadowRoot ?? null;
const stageText = () => shadow()?.querySelector(".stage")?.textContent ?? "";

function send(text: string) {
	(shadow()?.querySelector("textarea") as HTMLTextAreaElement).value = text;
	shadow()
		?.querySelector("form")
		?.dispatchEvent(new Event("submit", { cancelable: true }));
}

describe("widget with the assistant on", () => {
	let streams: Array<ReturnType<typeof controlledStream>>;
	let bodies: Array<Record<string, unknown>>;
	const body = (i: number): Record<string, unknown> => bodies[i] ?? {};
	const streamAt = (i: number) => {
		const found = streams[i];
		if (!found) throw new Error(`no stream ${i}`);
		return found;
	};

	beforeEach(async () => {
		vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "clearInterval", "Date"] });
		streams = [];
		bodies = [];
		window.sessionStorage.clear();
		window.history.pushState({}, "", "/pricing?utm_source=google&utm_campaign=diwali&x=1#top");
		vi.stubGlobal(
			"fetch",
			vi.fn(async (url: string, init?: { body?: string }) => {
				if (String(url).endsWith("/widget/config")) {
					return {
						ok: true,
						json: async () => ({
							title: "Shree Labs help",
							assistant_name: "Asha",
							ai_disclosure: "I'm Asha, the AI assistant of Shree Labs.",
							language: "en",
						}),
					};
				}
				if (String(url).endsWith("/widget/chat")) {
					bodies.push(JSON.parse(init?.body ?? "{}"));
					const s = controlledStream();
					streams.push(s);
					return s.response;
				}
				return { ok: false, status: 404, json: async () => ({}) };
			}),
		);
		const tag = document.createElement("script");
		tag.setAttribute("data-key", KEY);
		tag.src = "http://engine.test/widget.js";
		Object.defineProperty(document, "currentScript", { value: tag, configurable: true });
		loadWidgetScripts();
		await vi.advanceTimersByTimeAsync(0);
	});

	afterEach(() => {
		document.querySelector("[data-cobrag-widget]")?.remove();
		vi.unstubAllGlobals();
		vi.useRealTimers();
		window.sessionStorage.clear();
	});

	async function answer(first: boolean, meta: Record<string, unknown> = {}) {
		send(first ? "What are your timings?" : "And on Sunday?");
		await vi.advanceTimersByTimeAsync(0);
		const s = streamAt(streams.length - 1);
		s.push(sse("meta", { route: "grounded", label: "business_question", ...meta }));
		s.push(sse("token", { content: "We open at 9 am." }));
		s.push(sse("done", {}));
		s.close();
		await vi.advanceTimersByTimeAsync(20);
	}

	it("shows the AI disclosure under the title", () => {
		const sub = shadow()?.querySelector(".head .sub");
		expect(sub?.textContent).toBe("I'm Asha, the AI assistant of Shree Labs.");
	});

	it("stores the session from meta and sends it, the page and the campaign next time", async () => {
		await answer(true, { session: TOKEN });
		expect(window.sessionStorage.getItem(SESSION_STORE)).toBe(TOKEN);
		// The first message had no session; it still told the engine where the visitor is.
		expect(body(0).session).toBeNull();
		expect(body(0).page_url).toBe(`${window.location.origin}/pricing?utm_source=google&utm_campaign=diwali&x=1`);
		expect(body(0).utm).toEqual({ utm_source: "google", utm_campaign: "diwali" });

		await answer(false); // an existing chat's `meta` carries no new token
		expect(body(1).session).toBe(TOKEN);
		expect(window.sessionStorage.getItem(SESSION_STORE)).toBe(TOKEN);
	});

	it("starts a new chat when the visitor resets it", async () => {
		await answer(true, { session: TOKEN });
		shadow()?.querySelector<HTMLButtonElement>(".reset")?.click();
		expect(window.sessionStorage.getItem(SESSION_STORE)).toBeNull();
		await answer(false);
		expect(body(1).session).toBeNull();
	});

	it("says what the assistant is doing in plain words after a wait", async () => {
		send("Do you have a home collection?");
		await vi.advanceTimersByTimeAsync(0);
		const s = streamAt(0);
		for (const [stage, words] of [
			["check", "Double-checking the details…"],
			["handoff", "Finding the best way to reach us…"],
		] as const) {
			s.push(sse("stage", { stage, elapsed_ms: 100 }));
			await vi.advanceTimersByTimeAsync(2600);
			expect(stageText()).toBe(words);
		}
		s.close();
		await vi.advanceTimersByTimeAsync(20);
	});
});
