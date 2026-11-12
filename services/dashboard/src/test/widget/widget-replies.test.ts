// The widget's suggestion chips and call-to-action button, run for real against widget.js in
// jsdom: the owner's starters under the greeting, the chips and the one button after an answer,
// the tap and dismiss events, and the refusal to show an unsafe link. The script is served by the
// engine with no bundler, so the test evaluates its source.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadWidgetScripts } from "./load-widget";

const enc = new TextEncoder();
const KEY = "pk_testkey000000000000";
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
const chips = () => Array.from(shadow()?.querySelectorAll(".chip") ?? []);
const ctaButtons = () => Array.from(shadow()?.querySelectorAll(".cta-btn") ?? []);

function send(text: string) {
	(shadow()?.querySelector("textarea") as HTMLTextAreaElement).value = text;
	shadow()
		?.querySelector("form")
		?.dispatchEvent(new Event("submit", { cancelable: true }));
}

const STARTERS = [
	{ label: "Test prices", message: "What does a CBC cost?" },
	{ label: "Home pickup", message: "Do you collect samples at home?" },
];

describe("widget chips and call to action", () => {
	let streams: Array<ReturnType<typeof controlledStream>>;
	let chats: Array<Record<string, unknown>>;
	let events: Array<Record<string, unknown>>;

	beforeEach(async () => {
		vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "clearInterval", "Date"] });
		streams = [];
		chats = [];
		events = [];
		window.sessionStorage.clear();
		vi.stubGlobal(
			"fetch",
			vi.fn(async (url: string, init?: { body?: string }) => {
				if (String(url).endsWith("/widget/config")) {
					return {
						ok: true,
						json: async () => ({ title: "Shree Labs help", language: "en", starters: STARTERS }),
					};
				}
				if (String(url).endsWith("/widget/chat")) {
					chats.push(JSON.parse(init?.body ?? "{}"));
					const s = controlledStream();
					streams.push(s);
					return s.response;
				}
				if (String(url).endsWith("/widget/event")) {
					events.push(JSON.parse(init?.body ?? "{}"));
					return { ok: true, status: 204 };
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

	/** Feeds the last chat stream: meta, a token, then the given extra events, then done. */
	async function reply(extra: Array<[string, unknown]>, cta: unknown = null) {
		const s = streams[streams.length - 1];
		if (!s) throw new Error("no chat stream");
		s.push(sse("meta", { route: "grounded", label: "buying_intent", session: TOKEN }));
		s.push(sse("token", { content: "A CBC costs Rs 300." }));
		for (const [name, data] of extra) s.push(sse(name, data));
		s.push(sse("done", { label: "buying_intent", route: "grounded", language: "en", cta }));
		s.close();
		await vi.advanceTimersByTimeAsync(20);
	}

	it("shows the owner's starters under the greeting, and drops them after a first message", async () => {
		expect(chips().map((c) => c.textContent)).toEqual(["Test prices", "Home pickup"]);
		send("hello");
		await vi.advanceTimersByTimeAsync(0);
		expect(chips()).toHaveLength(0);
		await reply([]);
		expect(chips()).toHaveLength(0);
	});

	it("shows at most four chips and one call-to-action button after an answer", async () => {
		send("What does a CBC cost?");
		await vi.advanceTimersByTimeAsync(0);
		const many = Array.from({ length: 6 }, (_, i) => ({ label: `Chip ${i}`, message: `Question ${i}?` }));
		await reply([["quick_replies", { items: many }]], {
			type: "whatsapp",
			label: "Chat on WhatsApp",
			url: "https://wa.me/919812345678?text=Hi%21",
		});
		expect(chips()).toHaveLength(4);
		expect(ctaButtons()).toHaveLength(1);
		const link = ctaButtons()[0] as HTMLAnchorElement;
		expect(link.tagName).toBe("A");
		expect(link.getAttribute("href")).toBe("https://wa.me/919812345678?text=Hi%21");
		expect(link.getAttribute("target")).toBe("_blank");
		expect(link.getAttribute("rel")).toContain("noopener");
		expect(link.textContent).toBe("Chat on WhatsApp");
	});

	it("names the chip group and says what 'Not now' dismisses", async () => {
		send("What does a CBC cost?");
		await vi.advanceTimersByTimeAsync(0);
		await reply([["quick_replies", { items: [{ label: "Fasting rules", message: "Which tests need fasting?" }] }]], {
			type: "call",
			label: "Call us",
			url: "tel:+919812345678",
		});
		const group = shadow()?.querySelector('[role="group"]');
		expect(group?.getAttribute("aria-label")).toBe("Suggestions");
		expect(shadow()?.querySelector(".cta-x")?.getAttribute("aria-label")).toBe("Not now: Call us");
	});

	it("sends a tapped chip as a normal message and counts the tap", async () => {
		send("What does a CBC cost?");
		await vi.advanceTimersByTimeAsync(0);
		await reply([["quick_replies", { items: [{ label: "Fasting rules", message: "Which tests need fasting?" }] }]]);
		(chips()[0] as HTMLButtonElement).click();
		await vi.advanceTimersByTimeAsync(0);
		expect(chats[1]?.message).toBe("Which tests need fasting?");
		expect(events).toContainEqual({ session: TOKEN, type: "chip_click" });
		expect(chips()).toHaveLength(0); // the chips give way to the message
	});

	it("holds a starter tap made before any chat session and counts it after the first reply", async () => {
		(chips()[0] as HTMLButtonElement).click();
		await vi.advanceTimersByTimeAsync(0);
		expect(chats[0]?.message).toBe("What does a CBC cost?");
		expect(events).toHaveLength(0);
		await reply([]);
		expect(events).toContainEqual({ session: TOKEN, type: "chip_click" });
	});

	it("posts cta_click on a tap and cta_dismiss on 'Not now', and removes the button", async () => {
		send("What does a CBC cost?");
		await vi.advanceTimersByTimeAsync(0);
		await reply([], { type: "call", label: "Call us", url: "tel:+919812345678" });
		(ctaButtons()[0] as HTMLElement).dispatchEvent(new MouseEvent("click", { bubbles: true, composed: true }));
		await vi.advanceTimersByTimeAsync(0);
		expect(events).toContainEqual({ session: TOKEN, type: "cta_click", cta_type: "call" });
		const dismiss = shadow()?.querySelector<HTMLButtonElement>(".cta-x");
		dismiss?.click();
		await vi.advanceTimersByTimeAsync(0);
		expect(events).toContainEqual({ session: TOKEN, type: "cta_dismiss", cta_type: "call" });
		expect(ctaButtons()).toHaveLength(0);
	});

	it("renders nothing for an unsafe link", async () => {
		send("What does a CBC cost?");
		await vi.advanceTimersByTimeAsync(0);
		await reply([], { type: "link", label: "Click me", url: "javascript:alert(1)" });
		expect(ctaButtons()).toHaveLength(0);
	});

	it("makes lead_form and handoff buttons rather than links", async () => {
		send("What does a CBC cost?");
		await vi.advanceTimersByTimeAsync(0);
		await reply([], { type: "handoff", label: "Talk to a person", url: null });
		const btn = ctaButtons()[0] as HTMLElement;
		expect(btn.tagName).toBe("BUTTON");
		expect(btn.getAttribute("href")).toBeNull();
	});

	it("a handoff button asks for a person through the normal message path", async () => {
		send("What does a CBC cost?");
		await vi.advanceTimersByTimeAsync(0);
		await reply([], { type: "handoff", label: "Talk to a person", url: null });
		(ctaButtons()[0] as HTMLButtonElement).click();
		await vi.advanceTimersByTimeAsync(0);
		expect(chats[1]?.message).toBe("I'd like to talk to a person");
	});
});
