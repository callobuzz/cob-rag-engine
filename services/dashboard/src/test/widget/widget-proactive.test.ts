// The widget's proactive greeting, run for real against widget.js in jsdom: one bubble per page
// view after its delay, never over an open panel or after a message, frequency kept in storage,
// owner text rendered through the safe Markdown renderer.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadWidgetScripts } from "./load-widget";

const KEY = "pk_testkey000000000000";

type Greeting = {
	id: string;
	page_url_glob: string;
	delay_seconds: number;
	message: string;
	frequency: "once_per_session" | "once_per_day" | "always";
};

const GREETING: Greeting = {
	id: "home",
	page_url_glob: "/**",
	delay_seconds: 10,
	message: "Need help? **Ask me** anything.",
	frequency: "once_per_session",
};

function shadow(): ShadowRoot {
	const root = document.querySelector("[data-cobrag-widget]")?.shadowRoot;
	if (!root) throw new Error("widget not built");
	return root;
}
const nudge = () => shadow().querySelector(".nudge");

async function boot(proactive: Greeting[]) {
	vi.stubGlobal(
		"fetch",
		vi.fn(async (url: string) =>
			String(url).includes("/widget/config")
				? { ok: true, status: 200, json: async () => ({ proactive, language: "en" }) }
				: { ok: false, status: 404, json: async () => ({}) },
		),
	);
	const tag = document.createElement("script");
	tag.setAttribute("data-key", KEY);
	tag.src = "http://engine.test/widget.js";
	Object.defineProperty(document, "currentScript", { value: tag, configurable: true });
	loadWidgetScripts();
	await vi.advanceTimersByTimeAsync(0);
}

describe("widget proactive greeting", () => {
	beforeEach(() => {
		vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "clearInterval", "clearTimeout", "Date"] });
		window.sessionStorage.clear();
		window.localStorage.clear();
	});

	afterEach(() => {
		document.querySelector("[data-cobrag-widget]")?.remove();
		vi.unstubAllGlobals();
		vi.useRealTimers();
	});

	it("shows after its delay, rendered as Markdown, and only once per page view", async () => {
		await boot([GREETING, { ...GREETING, id: "second", message: "Second one" }]);
		expect(nudge()).toBeNull();
		await vi.advanceTimersByTimeAsync(9_000);
		expect(nudge()).toBeNull();
		await vi.advanceTimersByTimeAsync(1_500);
		expect(nudge()?.textContent).toContain("Need help? Ask me anything.");
		expect(nudge()?.querySelector("strong")?.textContent).toBe("Ask me");
		await vi.advanceTimersByTimeAsync(20_000);
		expect(shadow().querySelectorAll(".nudge").length).toBe(1);
		expect(window.sessionStorage.getItem("cobrag:pg:home")).toBe("1");
	});

	it("never builds HTML from the message", async () => {
		await boot([{ ...GREETING, delay_seconds: 0, message: '<img src=x onerror="window.pwned=1"> hi' }]);
		await vi.advanceTimersByTimeAsync(10);
		expect(nudge()?.querySelector("img")).toBeNull();
		expect(nudge()?.textContent).toContain("hi");
	});

	it("is not shown for another page or after its frequency is used up", async () => {
		await boot([{ ...GREETING, delay_seconds: 0, page_url_glob: "/tests/**" }]);
		await vi.advanceTimersByTimeAsync(10);
		expect(nudge()).toBeNull();
		document.querySelector("[data-cobrag-widget]")?.remove();

		await boot([{ ...GREETING, delay_seconds: 0 }]);
		await vi.advanceTimersByTimeAsync(10);
		expect(nudge()).not.toBeNull();
		document.querySelector("[data-cobrag-widget]")?.remove();
		await boot([{ ...GREETING, delay_seconds: 0 }]); // same session: already shown
		await vi.advanceTimersByTimeAsync(10);
		expect(nudge()).toBeNull();
	});

	it("stays away from an open panel", async () => {
		await boot([GREETING]);
		(shadow().querySelector(".launcher") as HTMLButtonElement).click();
		await vi.advanceTimersByTimeAsync(20_000);
		expect(nudge()).toBeNull();
	});

	it("stays away once the visitor has written", async () => {
		await boot([GREETING]);
		(shadow().querySelector("textarea") as HTMLTextAreaElement).value = "Hello";
		shadow()
			.querySelector("form")
			?.dispatchEvent(new Event("submit", { cancelable: true }));
		await vi.advanceTimersByTimeAsync(20_000);
		expect(nudge()).toBeNull();
	});

	it("opens the chat when tapped and goes away", async () => {
		await boot([{ ...GREETING, delay_seconds: 0 }]);
		await vi.advanceTimersByTimeAsync(10);
		(nudge()?.querySelector(".md") as HTMLElement | undefined)?.click();
		expect(shadow().querySelector(".panel")?.classList.contains("open")).toBe(true);
		expect(nudge()).toBeNull();
	});

	it("offers a focusable button that opens the chat, for keyboard users", async () => {
		await boot([{ ...GREETING, delay_seconds: 0 }]);
		await vi.advanceTimersByTimeAsync(10);
		const go = nudge()?.querySelector("button.go") as HTMLButtonElement | null;
		expect(go?.textContent).toBe("Open chat");
		go?.click();
		expect(shadow().querySelector(".panel")?.classList.contains("open")).toBe(true);
		expect(nudge()).toBeNull();
	});

	it("counts a day by the visitor's local calendar, not UTC", async () => {
		vi.setSystemTime(new Date(2026, 9, 3, 23, 30)); // local time, whatever the zone
		await boot([{ ...GREETING, delay_seconds: 0, frequency: "once_per_day" }]);
		await vi.advanceTimersByTimeAsync(10);
		expect(window.localStorage.getItem("cobrag:pg:home")).toBe("2026-10-03");
	});

	it("dismisses for good with Don't show again, even for an always greeting", async () => {
		await boot([{ ...GREETING, delay_seconds: 0, frequency: "always" }]);
		await vi.advanceTimersByTimeAsync(10);
		(nudge()?.querySelector(".off") as HTMLButtonElement | undefined)?.click();
		expect(nudge()).toBeNull();
		expect(window.localStorage.getItem("cobrag:pg:home:off")).toBe("1");
		document.querySelector("[data-cobrag-widget]")?.remove();
		await boot([{ ...GREETING, delay_seconds: 0, frequency: "always" }]);
		await vi.advanceTimersByTimeAsync(10);
		expect(nudge()).toBeNull();
	});

	it("survives storage that throws", async () => {
		const blocked = vi.spyOn(window.Storage.prototype, "getItem").mockImplementation(() => {
			throw new Error("denied");
		});
		const blockedSet = vi.spyOn(window.Storage.prototype, "setItem").mockImplementation(() => {
			throw new Error("denied");
		});
		await boot([{ ...GREETING, delay_seconds: 0 }]);
		await vi.advanceTimersByTimeAsync(10);
		expect(nudge()).not.toBeNull();
		blocked.mockRestore();
		blockedSet.mockRestore();
	});
});
