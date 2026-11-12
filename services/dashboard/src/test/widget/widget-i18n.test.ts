// The widget's own chrome in English, Hindi and Hinglish, run for real against widget.js in jsdom:
// the language follows the assistant's primary language, then the first reply's `meta.language`;
// "Powered by" never changes; an owner's own title or placeholder is never overwritten; a lead
// confirmation with a booking link shows it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadWidgetScripts } from "./load-widget";

const enc = new TextEncoder();
const KEY = "pk_testkey000000000000";
const TOKEN = `ws_${"a".repeat(43)}`;

function sse(event: string, data: unknown): Uint8Array {
	return enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

function streamOf(chunks: Uint8Array[], hang = false) {
	const queue = [...chunks];
	return {
		ok: true,
		status: 200,
		body: {
			getReader: () => ({
				read: async () => {
					const next = queue.shift();
					if (next) return { done: false, value: next };
					return hang ? new Promise<never>(() => {}) : { done: true };
				},
			}),
		},
	};
}

function shadow(): ShadowRoot {
	const root = document.querySelector("[data-cobrag-widget]")?.shadowRoot;
	if (!root) throw new Error("widget not built");
	return root;
}
const text = (selector: string) => shadow().querySelector(selector)?.textContent ?? "";
const attr = (selector: string, name: string) => shadow().querySelector(selector)?.getAttribute(name) ?? "";

describe("widget language", () => {
	let config: Record<string, unknown>;
	let chat: Uint8Array[];
	let hang = false;

	async function boot() {
		window.sessionStorage.clear();
		vi.stubGlobal(
			"fetch",
			vi.fn(async (url: string) => {
				const u = String(url);
				if (u.endsWith("/widget/config")) return { ok: true, status: 200, json: async () => config };
				if (u.endsWith("/widget/chat")) return streamOf(chat, hang);
				return { ok: false, status: 404, json: async () => ({}) };
			}),
		);
		const tag = document.createElement("script");
		tag.setAttribute("data-key", KEY);
		tag.src = "http://engine.test/widget.js";
		Object.defineProperty(document, "currentScript", { value: tag, configurable: true });
		loadWidgetScripts();
		await vi.advanceTimersByTimeAsync(0);
	}

	async function ask(question: string) {
		(shadow().querySelector("textarea") as HTMLTextAreaElement).value = question;
		shadow()
			.querySelector("form")
			?.dispatchEvent(new Event("submit", { cancelable: true }));
		await vi.advanceTimersByTimeAsync(50);
	}

	beforeEach(() => {
		vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "clearInterval", "Date"] });
		hang = false;
		config = {
			title: "Ask us anything",
			greeting: "Hi! How can I help you today?",
			placeholder: "Type your question…",
			language: "en",
		};
		chat = [
			sse("meta", { route: "answer", label: "business_question", session: TOKEN, language: "hi-Latn" }),
			sse("token", { content: "Hum subah 9 baje khulte hain." }),
			sse("done", {}),
		];
	});

	afterEach(() => {
		document.querySelector("[data-cobrag-widget]")?.remove();
		vi.unstubAllGlobals();
		vi.useRealTimers();
	});

	it("is English by default, with today's words", async () => {
		await boot();
		expect(text(".send")).toBe("Send");
		expect(attr("textarea", "placeholder")).toBe("Type your question…");
		expect(text("h2")).toBe("Ask us anything");
		expect(attr(".launcher", "aria-label")).toBe("Open chat");
	});

	it("starts in the assistant's primary language", async () => {
		config = { ...config, language: "hi" };
		await boot();
		expect(text(".send")).toBe("भेजें");
		expect(attr("textarea", "placeholder")).toBe("अपना सवाल लिखें…");
		expect(text("h2")).toBe("हमसे कुछ भी पूछें");
		expect(attr(".launcher", "aria-label")).toBe("चैट खोलें");
	});

	it("keeps a title and placeholder the owner wrote, in any language", async () => {
		config = { ...config, language: "hi", title: "Shree Labs help", placeholder: "Ask Shree Labs" };
		await boot();
		expect(text("h2")).toBe("Shree Labs help");
		expect(attr("textarea", "placeholder")).toBe("Ask Shree Labs");
		expect(text(".send")).toBe("भेजें");
	});

	it("follows the language of the first reply and relabels in place", async () => {
		await boot();
		(shadow().querySelector("textarea") as HTMLTextAreaElement).value = "kitne baje";
		await ask("Aap kitne baje khulte hain?");
		expect(text(".send")).toBe("Bhejein");
		expect(attr("textarea", "placeholder")).toBe("Apna sawaal likhiye…");
		expect(attr(".reset", "aria-label")).toBe("Nayi baatcheet shuru karein");
		expect(attr(".close", "aria-label")).toBe("Chat band karein");
		expect(attr(".launcher", "aria-label")).toBe("Chat kholein");
		expect(attr(".panel", "lang")).toBe("hi-Latn");
	});

	it("marks the panel and launcher with the chrome's language", async () => {
		await boot();
		expect(attr(".panel", "lang")).toBe("en");
		config = { ...config, language: "hi" };
		document.querySelector("[data-cobrag-widget]")?.remove();
		await boot();
		expect(attr(".panel", "lang")).toBe("hi");
		expect(attr(".launcher", "lang")).toBe("hi");
	});

	it("never translates the Powered-by line", async () => {
		config = { ...config, language: "hi" };
		await boot();
		expect(text(".powered")).toBe("cob-rag-engine by Callobuzz");
		chat = [sse("meta", { session: TOKEN, language: "hi-Latn" }), sse("token", { content: "ok" }), sse("done", {})];
		await ask("hello");
		expect(text(".powered")).toBe("cob-rag-engine by Callobuzz");
	});

	it("shows a stage in the visitor's language after the wait", async () => {
		config = { ...config, language: "hi" };
		hang = true; // the answer never arrives
		chat = [sse("meta", { session: TOKEN, language: "hi" }), sse("stage", { stage: "search", elapsed_ms: 10 })];
		await boot();
		await ask("समय क्या है");
		expect(text(".stage")).toBe("");
		await vi.advanceTimersByTimeAsync(2600);
		expect(text(".stage")).toBe("हमारे दस्तावेज़ खोजे जा रहे हैं…");
	});

	it("answers in the visitor's language when the stream carries no answer", async () => {
		config = { ...config, language: "hi" };
		chat = [sse("meta", { session: TOKEN, language: "hi" }), sse("done", {})];
		await boot();
		await ask("समय क्या है");
		expect(shadow().textContent).toContain("माफ़ कीजिए, इसका जवाब नहीं मिल सका।");
	});

	it("marks a Hindi reply for the roomier line height", async () => {
		chat = [sse("meta", { session: TOKEN, language: "hi" }), sse("token", { content: "नमस्ते" }), sse("done", {})];
		await boot();
		await ask("hi");
		const bots = shadow().querySelectorAll(".msg.bot");
		expect(bots.item(bots.length - 1)?.getAttribute("lang")).toBe("hi");
	});
});

describe("widget lead confirmation", () => {
	afterEach(() => {
		document.querySelector("[data-cobrag-widget]")?.remove();
		vi.unstubAllGlobals();
		vi.useRealTimers();
	});

	async function submitLead(confirmation: Record<string, unknown>, language: string) {
		vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "clearInterval", "Date"] });
		window.sessionStorage.clear();
		const form = {
			kind: "lead_form",
			form_id: `lf_${"c".repeat(16)}`,
			title: "Leave your number",
			notice: "We use it to call you.",
			consent_label: "I agree",
			privacy_url: null,
			fields: [{ id: "phone", label: "Mobile number", type: "tel", required: true, options: [] }],
			submit_label: "Send",
			trigger: "buying_intent",
		};
		vi.stubGlobal(
			"fetch",
			vi.fn(async (url: string) => {
				const u = String(url);
				if (u.endsWith("/widget/config")) return { ok: true, json: async () => ({ language }) };
				if (u.endsWith("/widget/chat")) {
					return streamOf([
						sse("meta", { session: TOKEN, language }),
						sse("token", { content: "ok" }),
						sse("ui", form),
						sse("done", {}),
					]);
				}
				if (u.endsWith("/widget/lead")) return { ok: true, status: 201, json: async () => ({ confirmation }) };
				return { ok: false, status: 404, json: async () => ({}) };
			}),
		);
		const tag = document.createElement("script");
		tag.setAttribute("data-key", KEY);
		tag.src = "http://engine.test/widget.js";
		Object.defineProperty(document, "currentScript", { value: tag, configurable: true });
		loadWidgetScripts();
		await vi.advanceTimersByTimeAsync(0);
		(shadow().querySelector("textarea") as HTMLTextAreaElement).value = "price?";
		shadow()
			.querySelector("form")
			?.dispatchEvent(new Event("submit", { cancelable: true }));
		await vi.advanceTimersByTimeAsync(50);
		const lead = shadow().querySelector(".lead form") as HTMLFormElement;
		(lead.querySelector('input[name="phone"]') as HTMLInputElement).value = "98765 43210";
		(shadow().querySelector(".lead .consent input") as HTMLInputElement).checked = true;
		lead.dispatchEvent(new Event("submit", { cancelable: true }));
		await vi.advanceTimersByTimeAsync(0);
	}

	it("shows the booking link of a qualified lead, in the visitor's language", async () => {
		await submitLead(
			{ message: "Thanks!", withdraw_url: "https://x.test/w#t=1", booking_url: "https://shreelabs.in/book" },
			"hi-Latn",
		);
		const book = shadow().querySelector(".lead a.book") as HTMLAnchorElement | null;
		expect(book?.getAttribute("href")).toBe("https://shreelabs.in/book");
		expect(book?.textContent).toBe("Abhi book karein");
		expect(book?.rel).toContain("noopener");
	});

	it("shows no booking button without a link or with an unsafe one", async () => {
		await submitLead({ message: "Thanks!", withdraw_url: "https://x.test/w#t=1" }, "en");
		expect(shadow().querySelector(".lead a.book")).toBeNull();
		document.querySelector("[data-cobrag-widget]")?.remove();
		await submitLead(
			{ message: "Thanks!", withdraw_url: "https://x.test/w#t=1", booking_url: "javascript:alert(1)" },
			"en",
		);
		expect(shadow().querySelector(".lead a.book")).toBeNull();
	});
});
