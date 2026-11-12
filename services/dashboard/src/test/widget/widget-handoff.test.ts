// The widget's handoff card, run for real against widget.js in jsdom: the card an assistant reply
// carries, the refusal to show an unsafe link, the report of a tap (once, with the chat session), the
// call-back form's day and slot sent as `callback_window`, and the card coming back after a reload.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadWidgetScripts } from "./load-widget";

const enc = new TextEncoder();
const KEY = "pk_testkey000000000000";
const TOKEN = `ws_${"a".repeat(43)}`;
const STORE = `cobrag:${KEY.slice(0, 16)}`;

function sse(event: string, data: unknown): Uint8Array {
	return enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

function streamOf(chunks: Uint8Array[]) {
	const queue = [...chunks];
	return {
		ok: true,
		status: 200,
		body: {
			getReader: () => ({
				read: async () => {
					const next = queue.shift();
					return next ? { done: false, value: next } : { done: true };
				},
			}),
		},
	};
}

const CALLBACK_FORM = {
	kind: "lead_form",
	form_id: `lf_${"b".repeat(16)}`,
	title: "Request a call back",
	notice: "We use your number only to call you.",
	consent_label: "I agree",
	privacy_url: null,
	fields: [
		{ id: "name", label: "Your name", type: "text", required: false, options: [] },
		{ id: "phone", label: "Mobile number", type: "tel", required: true, options: [] },
		{ id: "callback_date", label: "Preferred day", type: "date", required: true, options: [] },
		{
			id: "callback_window",
			label: "Preferred time",
			type: "select",
			required: true,
			options: ["09:00-12:00", "12:00-15:00", "15:00-18:00"],
		},
	],
	submit_label: "Send",
	trigger: "callback",
	callback: true,
};

const CARD = {
	kind: "handoff_card",
	open_now: false,
	message: "We're closed now. **WhatsApp** us and we'll reply when we open.",
	reason: "explicit_request",
	channels: [
		{ kind: "whatsapp", label: "WhatsApp us", url: "https://wa.me/919812345678?text=Hi%21" },
		{ kind: "email", label: "Email us", url: "javascript:alert(1)" },
		{ kind: "callback", label: "Call me back", url: null },
	],
	callback_form: CALLBACK_FORM,
};

const shadow = () => document.querySelector("[data-cobrag-widget]")?.shadowRoot ?? null;
const chans = () => Array.from(shadow()?.querySelectorAll(".card .chan") ?? []);

function send(text: string) {
	(shadow()?.querySelector("textarea") as HTMLTextAreaElement).value = text;
	shadow()
		?.querySelector("form")
		?.dispatchEvent(new Event("submit", { cancelable: true }));
}

describe("widget handoff card", () => {
	let handoffs: Array<{ body: Record<string, unknown>; init: Record<string, unknown> }>;
	let leads: Array<Record<string, unknown>>;
	let chats: Array<Record<string, unknown>>;
	let reply: Uint8Array[];

	async function boot(history: unknown[] = []) {
		window.sessionStorage.clear();
		if (history.length) window.sessionStorage.setItem(STORE, JSON.stringify(history));
		window.sessionStorage.setItem(`${STORE}:session`, TOKEN);
		const tag = document.createElement("script");
		tag.setAttribute("data-key", KEY);
		tag.src = "http://engine.test/widget.js";
		Object.defineProperty(document, "currentScript", { value: tag, configurable: true });
		loadWidgetScripts();
		await vi.advanceTimersByTimeAsync(0);
		// A tap on a link must not try to navigate jsdom.
		document.querySelector("[data-cobrag-widget]")?.addEventListener("click", (e) => {
			e.preventDefault();
		});
	}

	beforeEach(() => {
		vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "clearInterval", "Date"] });
		handoffs = [];
		leads = [];
		chats = [];
		reply = [
			sse("meta", { route: "handoff", label: "human_request", session: TOKEN, language: "en" }),
			sse("token", { content: "Sure, here is how to reach us." }),
			sse("ui", CARD),
			sse("done", { label: "human_request", route: "handoff", language: "en", cta: null }),
		];
		vi.stubGlobal(
			"fetch",
			vi.fn(async (url: string, init?: { body?: string; headers?: Record<string, string> }) => {
				const u = String(url);
				if (u.endsWith("/widget/config")) {
					return { ok: true, json: async () => ({ title: "Shree Labs help", language: "en" }) };
				}
				if (u.endsWith("/widget/chat")) {
					chats.push(JSON.parse(init?.body ?? "{}"));
					return streamOf(reply);
				}
				if (u.endsWith("/widget/handoff")) {
					handoffs.push({
						body: JSON.parse(init?.body ?? "{}"),
						init: init as unknown as Record<string, unknown>,
					});
					return { ok: true, status: 201, json: async () => ({ handoff_id: "h1" }) };
				}
				if (u.endsWith("/widget/lead")) {
					leads.push(JSON.parse(init?.body ?? "{}"));
					return {
						ok: true,
						status: 201,
						json: async () => ({ confirmation: { message: "Thanks!", withdraw_url: "https://x.test/w#t=1" } }),
					};
				}
				return { ok: false, status: 404, json: async () => ({}) };
			}),
		);
	});

	afterEach(() => {
		document.querySelector("[data-cobrag-widget]")?.remove();
		vi.unstubAllGlobals();
		vi.useRealTimers();
		window.sessionStorage.clear();
	});

	async function ask() {
		await boot();
		send("Can I talk to someone?");
		await vi.advanceTimersByTimeAsync(20);
	}

	it("shows the card after the answer, with safe links and a callback button", async () => {
		await ask();
		const cards = shadow()?.querySelectorAll(".card") ?? [];
		expect(cards).toHaveLength(1);
		expect(shadow()?.querySelector(".card-msg strong")?.textContent).toBe("WhatsApp");
		const links = Array.from(shadow()?.querySelectorAll(".card a.chan") ?? []) as HTMLAnchorElement[];
		expect(links.map((a) => a.textContent)).toEqual(["WhatsApp us"]); // the javascript: link is dropped
		expect(links[0]?.getAttribute("href")).toBe("https://wa.me/919812345678?text=Hi%21");
		expect(links[0]?.getAttribute("rel")).toBe("noopener noreferrer");
		expect(shadow()?.querySelector(".card button.chan")?.textContent).toBe("Call me back");
		expect(shadow()?.innerHTML).not.toContain("javascript:");
		// The card sits inside the assistant's bubble, under the answer.
		expect(cards[0]?.closest(".msg.bot")?.textContent).toContain("Sure, here is how to reach us.");
	});

	it("reports a tap on a link once, with the chat session, and lets the link go", async () => {
		await ask();
		const link = shadow()?.querySelector(".card a.chan") as HTMLAnchorElement;
		link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
		await vi.advanceTimersByTimeAsync(0);
		expect(handoffs).toHaveLength(1);
		expect(handoffs[0]?.body).toEqual({ session: TOKEN, channel: "whatsapp" });
		const init = handoffs[0]?.init as { keepalive: boolean; headers: Record<string, string> };
		expect(init.keepalive).toBe(true);
		expect(init.headers["X-Widget-Key"]).toBe(KEY);
	});

	it("opens the call-back form from the card and sends the day and slot as callback_window", async () => {
		await ask();
		(shadow()?.querySelector(".card button.chan") as HTMLButtonElement | null)?.click();
		const form = shadow()?.querySelector(".lead form") as HTMLFormElement;
		expect(form).toBeTruthy();
		const day = form.querySelector('input[name="callback_date"]') as HTMLInputElement;
		expect(day.type).toBe("date");
		expect(day.min).toMatch(/^\d{4}-\d{2}-\d{2}$/); // no day that has passed
		(form.querySelector('input[name="phone"]') as HTMLInputElement).value = "98765 43210";
		day.value = "2026-10-06";
		(form.querySelector('select[name="callback_window"]') as HTMLSelectElement).value = "15:00-18:00";
		(shadow()?.querySelector(".lead .consent input") as HTMLInputElement).checked = true;
		form.dispatchEvent(new Event("submit", { cancelable: true }));
		await vi.advanceTimersByTimeAsync(0);
		expect(leads).toHaveLength(1);
		expect(leads[0]).toMatchObject({
			session: TOKEN,
			form_id: CALLBACK_FORM.form_id,
			consent: true,
			callback_window: { date: "2026-10-06", start: "15:00", end: "18:00" },
		});
		// the pair is not a form field, and tapping "Call me back" never reports a handoff itself
		expect(Object.keys(leads[0]?.fields as object).sort()).toEqual(["phone"]);
		expect(handoffs).toHaveLength(0);
	});

	it("asks for a day and slot before it sends anything", async () => {
		await ask();
		(shadow()?.querySelector(".card button.chan") as HTMLButtonElement | null)?.click();
		const form = shadow()?.querySelector(".lead form") as HTMLFormElement;
		(form.querySelector('input[name="phone"]') as HTMLInputElement).value = "98765 43210";
		(shadow()?.querySelector(".lead .consent input") as HTMLInputElement).checked = true;
		form.dispatchEvent(new Event("submit", { cancelable: true }));
		await vi.advanceTimersByTimeAsync(0);
		expect(leads).toHaveLength(0);
		expect(form.querySelectorAll('[aria-invalid="true"]').length).toBeGreaterThan(0);
	});

	it("brings the card back when the chat is restored after a reload", async () => {
		await boot([
			{ role: "user", content: "Can I talk to someone?" },
			{ role: "assistant", content: "Sure.", sources: [], ui: CARD },
		]);
		expect(chans().map((c) => c.textContent)).toEqual(["WhatsApp us", "Call me back"]);
	});

	it("keeps an unsafe link out of a restored card too", async () => {
		const evil = { ...CARD, channels: [{ kind: "booking", label: "Book", url: "data:text/html,x" }] };
		await boot([{ role: "assistant", content: "Sure.", sources: [], ui: evil }]);
		expect(shadow()?.querySelectorAll(".card a")).toHaveLength(0);
	});

	it("the handoff button of a call-to-action asks to talk to a person, in the visitor's language", async () => {
		reply = [
			sse("meta", { route: "grounded", label: "buying_intent", session: TOKEN, language: "hi-Latn" }),
			sse("token", { content: "Ji, zaroor." }),
			sse("done", {
				label: "buying_intent",
				route: "grounded",
				language: "hi-Latn",
				cta: { type: "handoff", label: "Insaan se baat karein", url: null },
			}),
		];
		await boot();
		send("price?");
		await vi.advanceTimersByTimeAsync(20);
		(shadow()?.querySelector(".cta-btn") as HTMLButtonElement | null)?.click();
		await vi.advanceTimersByTimeAsync(20);
		expect(chats.map((c) => c.message)).toEqual(["price?", "Mujhe kisi insaan se baat karni hai"]);
	});
});
