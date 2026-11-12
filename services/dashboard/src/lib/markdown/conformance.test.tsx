// @vitest-environment jsdom
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";
import { parseMarkdown } from "./parse";
import { chunks } from "./stream";

interface Fixture {
	name: string;
	input: string;
	html: string;
	sources?: number[];
	streaming?: boolean;
	mode?: "inline";
}

const ROOT = path.resolve(import.meta.dirname, "../../../../..");
const FIXTURES: Fixture[] = JSON.parse(
	fs.readFileSync(path.join(ROOT, "tests/fixtures/markdown/conformance.json"), "utf8"),
);
const WIDGET_SRC = fs.readFileSync(path.join(ROOT, "src/callobuzz_rag/widget/static/markdown.js"), "utf8");

interface Widget {
	render(text: string, opts: object): string;
	inline(text: string, opts: object): string;
	stream(host: HTMLElement, opts: object): { update(text: string, final?: boolean): void };
}
const widget = (() => {
	const win: { CobMd?: Widget } = {};
	vm.runInNewContext(WIDGET_SRC, { window: win });
	return win.CobMd as Widget;
})();

/** React's static markup in the contract's canonical form: no wrapper, `'` as `&#39;`, void tags bare. */
function canonical(html: string): string {
	return html
		.replace(/^<(?:div|span) class="md[^"]*">/, "")
		.replace(/<\/(?:div|span)>$/, "")
		.replace(/&#x27;/g, "&#39;")
		.replace(/<br\/>/g, "<br>")
		.replace(/<hr\/>/g, "<hr>");
}

function dashboard(f: Fixture): string {
	return canonical(
		renderToStaticMarkup(
			<Markdown
				text={f.input}
				streaming={f.streaming}
				sources={f.sources}
				inline={f.mode === "inline"}
				copyable={false}
			/>,
		),
	);
}

describe("conformance fixtures (shared with the widget)", () => {
	it("has at least 60 cases", () => {
		expect(FIXTURES.length).toBeGreaterThanOrEqual(60);
	});

	for (const f of FIXTURES) {
		it(`dashboard: ${f.name}`, () => {
			expect(dashboard(f)).toBe(f.html);
		});
	}

	for (const f of FIXTURES) {
		it(`widget and dashboard agree: ${f.name}`, () => {
			const opts = { sources: f.sources, streaming: !!f.streaming };
			const w = f.mode === "inline" ? widget.inline(f.input, opts) : widget.render(f.input, opts);
			expect(w).toBe(dashboard(f));
		});
	}

	it("the widget's DOM builder serialises to the same HTML as its string renderer", () => {
		for (const f of FIXTURES.filter((x) => !x.mode && !x.streaming)) {
			const host = document.createElement("div");
			const s = widget.stream(host, { sources: f.sources });
			s.update(f.input, true);
			const html = [...host.querySelectorAll(".md-stable, .md-tail")].map((n) => n.innerHTML).join("");
			expect(html.replace(/&#39;/g, "'").replace(/&quot;/g, '"')).toBe(
				widget
					.render(f.input, { sources: f.sources })
					.replace(/&#39;/g, "'")
					.replace(/&quot;/g, '"'),
			);
		}
	});
});

describe("streaming", () => {
	const corpus = FIXTURES.filter((f) => !f.mode).map((f) => f.input);

	it("splitting into stable chunks never changes the blocks", () => {
		for (const text of corpus) {
			const { stable, tail } = chunks(text);
			const parts = [...stable, tail].flatMap((p) => parseMarkdown(p));
			expect(parts, text).toEqual(parseMarkdown(text));
		}
	});

	it("every prefix of every fixture splits consistently and stable chunks only append", () => {
		for (const text of corpus) {
			let before: string[] = [];
			for (let n = 0; n <= text.length; n++) {
				const { stable, tail } = chunks(text.slice(0, n));
				expect(
					before.every((c, i) => stable[i] === c),
					`${text.slice(0, n)}`,
				).toBe(true);
				before = stable;
				const parts = [...stable, tail].flatMap((p) => parseMarkdown(p));
				expect(parts, text.slice(0, n)).toEqual(parseMarkdown(text.slice(0, n)));
			}
		}
	});

	it("provisional rendering never throws and never shows a half-drawn table", () => {
		for (const text of corpus) {
			for (let n = 0; n <= text.length; n++) {
				const html = renderToStaticMarkup(<Markdown text={text.slice(0, n)} streaming copyable={false} />);
				// a table is whole (head + optional body) or absent: never an unclosed element
				const opens = (html.match(/<table/g) ?? []).length;
				const closes = (html.match(/<\/table>/g) ?? []).length;
				expect(opens).toBe(closes);
			}
		}
	});

	it("a long answer stays fast", () => {
		const para = "word **bold** and `code` and [link](https://example.com) [1] text. ".repeat(40);
		const big = Array.from({ length: 300 }, () => para).join("\n\n");
		const t0 = performance.now();
		renderToStaticMarkup(<Markdown text={big} sources={[1]} copyable={false} />);
		expect(performance.now() - t0).toBeLessThan(3000);
		const t1 = performance.now();
		parseMarkdown("*a ".repeat(3000) + "**b ".repeat(3000) + "_c ".repeat(3000) + "`d ".repeat(1000), {
			streaming: true,
		});
		expect(performance.now() - t1).toBeLessThan(3000);
	});
});
