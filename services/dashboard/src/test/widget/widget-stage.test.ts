// The embeddable widget's wait indicator, run for real against widget.js in jsdom: the stage
// words ("Searching your documents...") stay hidden for the first 2 s, appear after that, and
// give way to the answer on the first token. The script is served by the engine and shared
// with no bundler, so the test evaluates its source.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadWidgetScripts } from "./load-widget";

const enc = new TextEncoder();

function sse(event: string, data: unknown): Uint8Array {
	return enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

/** A response whose body the test feeds by hand. */
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

function stageText(): string {
	const host = document.querySelector("[data-cobrag-widget]");
	return host?.shadowRoot?.querySelector(".stage")?.textContent ?? "";
}
function lastBot(): Element | null {
	const bots = document.querySelector("[data-cobrag-widget]")?.shadowRoot?.querySelectorAll(".msg.bot");
	return bots?.item(bots.length - 1) ?? null;
}

describe("widget wait indicator", () => {
	let stream: ReturnType<typeof controlledStream>;

	beforeEach(async () => {
		vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "clearInterval", "Date"] });
		stream = controlledStream();
		vi.stubGlobal(
			"fetch",
			vi.fn(async (url: string) =>
				String(url).endsWith("/widget/chat") ? stream.response : { ok: false, status: 404, json: async () => ({}) },
			),
		);
		const tag = document.createElement("script");
		tag.setAttribute("data-key", "pk_testkey000000000000");
		tag.src = "http://engine.test/widget.js";
		Object.defineProperty(document, "currentScript", { value: tag, configurable: true });
		loadWidgetScripts();
		await vi.advanceTimersByTimeAsync(0);
	});

	afterEach(() => {
		document.querySelector("[data-cobrag-widget]")?.remove();
		vi.unstubAllGlobals();
		vi.useRealTimers();
	});

	it("hides the stage for 2 s, shows it after, and drops it on the first token", async () => {
		const shadow = document.querySelector("[data-cobrag-widget]")?.shadowRoot;
		const input = shadow?.querySelector("textarea") as HTMLTextAreaElement;
		input.value = "What are your opening hours?";
		shadow?.querySelector("form")?.dispatchEvent(new Event("submit", { cancelable: true }));
		await vi.advanceTimersByTimeAsync(0);

		stream.push(sse("stage", { stage: "search", elapsed_ms: 40 }));
		await vi.advanceTimersByTimeAsync(1400);
		expect(stageText()).toBe("");

		await vi.advanceTimersByTimeAsync(500); // 1.9 s since send
		expect(stageText()).toBe("");

		await vi.advanceTimersByTimeAsync(600); // past 2 s, next 500 ms tick
		expect(stageText()).toBe("Searching your documents…");

		stream.push(sse("stage", { stage: "write", elapsed_ms: 2600 }));
		await vi.advanceTimersByTimeAsync(10);
		expect(stageText()).toBe("Writing the answer…");

		stream.push(sse("token", { content: "We open at 9 am." }));
		await vi.advanceTimersByTimeAsync(10);
		expect(lastBot()?.querySelector(".stage")).toBeNull();
		expect(lastBot()?.textContent).toContain("We open at 9 am.");

		stream.push(sse("done", {}));
		stream.close();
		await vi.advanceTimersByTimeAsync(10);
	});

	it("never shows stage words for a quick answer", async () => {
		const shadow = document.querySelector("[data-cobrag-widget]")?.shadowRoot;
		(shadow?.querySelector("textarea") as HTMLTextAreaElement).value = "Hi";
		shadow?.querySelector("form")?.dispatchEvent(new Event("submit", { cancelable: true }));
		await vi.advanceTimersByTimeAsync(0);

		stream.push(sse("stage", { stage: "understand", elapsed_ms: 5 }));
		await vi.advanceTimersByTimeAsync(800);
		expect(stageText()).toBe("");
		stream.push(sse("token", { content: "Hello!" }));
		await vi.advanceTimersByTimeAsync(10);
		expect(lastBot()?.querySelector(".stage")).toBeNull();
		stream.close();
		await vi.advanceTimersByTimeAsync(10);
	});
});
