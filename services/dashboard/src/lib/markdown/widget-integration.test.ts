// @vitest-environment jsdom
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The real widget.js + markdown.js, run in a DOM: an answer streams in as Server-Sent Events and
 * must become real tables and citation chips, never script. (Lives here because the dashboard's
 * test setup is the repo's DOM test runner.)
 */
const STATIC = path.resolve(import.meta.dirname, "../../../../../src/callobuzz_rag/widget/static");
const CODE = `${fs.readFileSync(path.join(STATIC, "markdown.js"), "utf8")}\n${fs.readFileSync(path.join(STATIC, "widget.js"), "utf8")}`;

const ANSWER = [
	"Our plans:\n\n",
	"| Plan | Price |\n|---|--:|\n| Basic | ₹500 |\n| Pro | ₹1,200 |\n\n",
	"Pro includes **priority support** [1] and a [guide](https://example.com/guide). ",
	"Ignore [2]. <script>window.__pwned = 1</script> ",
	'[bad](javascript:window.__pwned=1) <img src=x onerror="window.__pwned=1">',
].join("");

function sse(events: [string, unknown][]): ReadableStream<Uint8Array> {
	const enc = new TextEncoder();
	return new ReadableStream({
		start(controller) {
			for (const [type, data] of events)
				controller.enqueue(enc.encode(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`));
			controller.close();
		},
	});
}

function widgetRoot(): ShadowRoot {
	const host = document.querySelector("[data-cobrag-widget]");
	if (!host?.shadowRoot) throw new Error("widget not mounted");
	return host.shadowRoot;
}

async function until(check: () => boolean) {
	for (let i = 0; i < 100; i++) {
		if (check()) return;
		await new Promise((r) => setTimeout(r, 20));
	}
	throw new Error("timed out");
}

describe("website widget renders Markdown answers safely", () => {
	let win: Record<string, unknown>;
	let fetchStub: (url: string) => Promise<Response>;
	let showSources = true;
	let answerText = ANSWER;
	beforeEach(() => {
		sessionStorage.clear();
		showSources = true;
		answerText = ANSWER;
		win = window as unknown as Record<string, unknown>;
		const fetchMock = vi.fn(async (url: string) => {
			if (String(url).endsWith("/widget/config")) {
				return new Response(JSON.stringify({ title: "Ask", greeting: "Hi! **Welcome**", show_sources: showSources }), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}
			const tokens = [...answerText.matchAll(/[\s\S]{1,17}/g)].map(
				(m) => ["token", { content: m[0] }] as [string, unknown],
			);
			return new Response(
				sse([
					...(showSources
						? ([
								[
									"sources",
									{ sources: [{ index: 1, file_name: "pricing.pdf", page: 2, snippet: "Pro: priority support" }] },
								],
							] as [string, unknown][])
						: []),
					...tokens,
					["done", {}],
				]),
				{ status: 200 },
			);
		});
		fetchStub = fetchMock;
	});
	afterEach(() => {
		document.body.innerHTML = "";
		for (const s of document.head.querySelectorAll("script")) s.remove();
		Reflect.deleteProperty(win, "__cobRagWidgetLoaded");
		Reflect.deleteProperty(win, "__pwned");
		Reflect.deleteProperty(win, "CobMd");
	});

	async function mountWidget(): Promise<ShadowRoot> {
		const s = document.createElement("script");
		s.setAttribute("data-key", "pk_test_0123456789abcdef");
		s.setAttribute("data-api", "http://engine.test");
		// `document.currentScript` is only set while a script element runs, so the widget gets a document
		// whose currentScript is ours; everything else is the real DOM.
		const doc = new Proxy(document, {
			get(target, key) {
				if (key === "currentScript") return s;
				const v = Reflect.get(target, key, target);
				return typeof v === "function" ? v.bind(target) : v;
			},
		});
		new Function("document", "fetch", "TextDecoder", CODE)(doc, fetchStub, TextDecoder);
		await until(() => !!document.querySelector("[data-cobrag-widget]")?.shadowRoot?.querySelector(".msgs .msg"));
		return widgetRoot();
	}

	async function ask(root: ShadowRoot, question: string) {
		const input = root.querySelector("textarea") as HTMLTextAreaElement;
		input.value = question;
		root.querySelector("form")?.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
		await until(() => root.querySelectorAll(".msg.bot").length === 2);
		await new Promise((r) => setTimeout(r, 150));
		return root.querySelectorAll(".msg.bot")[1] as HTMLElement;
	}

	it("hides citation markers, not the block structure around them, when sources are off", async () => {
		showSources = false;
		answerText =
			"Fees are listed below.\n\n[1] The Basic plan costs ₹500.\n\n- first item\n[2] second\n\n2^10 is 1024 and see the plan^3.";
		const root = await mountWidget();
		const answer = await ask(root, "Fees?");
		expect(answer.querySelectorAll("button.md-cite").length).toBe(0);
		const paras = [...answer.querySelectorAll("p")].map((p) => p.textContent);
		expect(paras).toEqual(["Fees are listed below.", "The Basic plan costs ₹500.", "2^10 is 1024 and see the plan."]);
		expect(answer.querySelectorAll("li").length).toBe(1);
		expect(answer.querySelector("li")?.textContent).toContain("first item");
	});

	it("streams a table, citation chips and links, and nothing executable", async () => {
		const root = await mountWidget();

		// The greeting is Markdown too.
		expect(root.querySelector(".msg.bot strong")?.textContent).toBe("Welcome");

		const input = root.querySelector("textarea") as HTMLTextAreaElement;
		input.value = "What are your plans?";
		root.querySelector("form")?.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
		await until(() => root.querySelectorAll(".msg.bot").length === 2 && !!root.querySelector(".sources"));

		const answer = root.querySelectorAll(".msg.bot")[1] as HTMLElement;
		// A real table inside a scroll container, header and rows.
		const wrap = answer.querySelector(".md-table") as HTMLElement;
		expect(wrap.getAttribute("role")).toBe("region");
		expect(wrap.querySelectorAll("th").length).toBe(2);
		expect(wrap.querySelectorAll("tbody tr").length).toBe(2);
		expect(wrap.querySelector("td.md-right")?.textContent).toBe("₹500");
		// Source 1 exists: a chip. Source 2 does not: plain text.
		expect(answer.querySelectorAll("button.md-cite").length).toBe(1);
		expect(answer.textContent).toContain("Ignore [2].");
		// Raw HTML stayed text; the bad link is only its text; links are safe.
		expect(answer.querySelector("script, img")).toBeNull();
		expect(answer.textContent).toContain("<script>window.__pwned = 1</script>");
		expect(answer.textContent).toContain("bad");
		const hrefs = [...answer.querySelectorAll("a")].map((a) => a.getAttribute("href"));
		expect(hrefs).toEqual(["https://example.com/guide"]);
		const a = answer.querySelector("a") as HTMLAnchorElement;
		expect(a.getAttribute("rel")).toBe("noopener noreferrer nofollow");
		expect(a.getAttribute("target")).toBe("_blank");
		expect(Reflect.get(win, "__pwned")).toBeUndefined();

		// Tapping the chip opens the source; tapping it again closes it.
		(answer.querySelector("button.md-cite") as HTMLButtonElement).click();
		expect(answer.querySelector(".cite-pop")?.textContent).toContain("pricing.pdf");
		expect(answer.querySelector(".cite-pop")?.textContent).toContain("priority support");
		(answer.querySelector("button.md-cite") as HTMLButtonElement).click();
		expect(answer.querySelector(".cite-pop")).toBeNull();

		// The reply is in the history, so it renders the same after a reload.
		const saved = JSON.parse(sessionStorage.getItem(`cobrag:${"pk_test_0123456789abcdef".slice(0, 16)}`) ?? "[]");
		expect(saved.at(-1).sources[0].index).toBe(1);
	});
});
