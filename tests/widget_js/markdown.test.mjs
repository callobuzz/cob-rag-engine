// The widget's embedded Markdown renderer (window.CobMd) against the shared conformance fixtures
// that the dashboard's renderer also passes. Run: node --test tests/widget_js/*.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SRC_URL = new URL("../../src/callobuzz_rag/widget/static/markdown.js", import.meta.url);
const FIXTURES = JSON.parse(fs.readFileSync(new URL("../fixtures/markdown/conformance.json", import.meta.url), "utf8"));
const source = fs.readFileSync(SRC_URL, "utf8");

function load() {
	const win = {};
	vm.runInNewContext(source, { window: win });
	return win.CobMd;
}
const CobMd = load();

function run(c) {
	const opts = { sources: c.sources, streaming: !!c.streaming };
	return c.mode === "inline" ? CobMd.inline(c.input, opts) : CobMd.render(c.input, opts);
}

test("the fixture file has at least 60 cases and unique names", () => {
	assert.ok(FIXTURES.length >= 60);
	assert.equal(new Set(FIXTURES.map((c) => c.name)).size, FIXTURES.length);
});

for (const c of FIXTURES) {
	test(`conformance: ${c.name}`, () => {
		assert.equal(run(c), c.html);
	});
}

test("markdown.js stays small, ES5 and dependency-free", () => {
	assert.ok(Buffer.byteLength(source) <= 24 * 1024, `markdown.js is ${Buffer.byteLength(source)} bytes`);
	const banned = [/\beval\s*\(/, /new Function/, /innerHTML/, /outerHTML/, /insertAdjacentHTML/, /document\.write/, /\bimport\b/, /\brequire\s*\(/, /=>/, /\blet\b/, /\bconst\b/];
	for (const bad of banned) assert.doesNotMatch(source, bad, String(bad));
});

test("every output is inert: only our own tags, no handler or style attribute, safe hrefs", () => {
	const tags = "p|h[3-6]|strong|em|del|code|pre|br|hr|ul|ol|li|blockquote|a|button|div|table|thead|tbody|tr|th|td";
	const unknownTag = new RegExp(`<(?!/?(?:${tags})[ >])`);
	for (const c of FIXTURES) {
		const html = run(c);
		assert.doesNotMatch(html, unknownTag, c.name);
		const tagsOnly = (html.match(/<[^>]*>/g) || []).join("");
		assert.doesNotMatch(tagsOnly, /\son[a-z]+=/i, c.name);
		assert.doesNotMatch(tagsOnly, /\sstyle=/i, c.name);
		for (const m of html.matchAll(/ href="([^"]*)"/g)) assert.match(m[1], /^(https?:|mailto:|tel:)/i, c.name);
	}
});

test("safeUrl", () => {
	const ok = ["https://example.com", "HTTP://example.com/a", "mailto:a@b.co", "tel:+919876543210"];
	for (const u of ok) assert.equal(CobMd.safeUrl(u), u, u);
	// Entities are decoded only to check the scheme; the target comes back as written.
	for (const u of ["https://x.com/p?id=1&tab=pricing", "https://x.com/p?price&lt=500", "https://x.com/?q=a&amp;b=2"]) {
		assert.equal(CobMd.safeUrl(u), u, u);
	}
	const bad = [
		"",
		"javascript:alert(1)",
		" JaVaScRiPt:1",
		"data:text/html,x",
		"vbscript:x",
		"//evil.example",
		"/a",
		"www.example.com",
		"https:///x",
		"ftp://x.example",
		"&#106;avascript:1",
		"java\tscript:1",
		"&amp;#106;avascript:1",
	];
	for (const u of bad) assert.equal(CobMd.safeUrl(u), null, u);
});

test("streaming: stable html only ever grows, and the end state equals a one-shot render", () => {
	for (const c of FIXTURES.filter((f) => !f.mode && !f.streaming)) {
		let state = null;
		let lastStable = "";
		const step = Math.max(1, Math.floor(c.input.length / 12));
		for (let n = 1; n <= c.input.length; n += step) {
			const r = CobMd.renderStream(state, c.input.slice(0, n), { sources: c.sources });
			assert.ok(r.html.startsWith(r.state.html), c.name);
			assert.ok(r.state.html.startsWith(lastStable), `${c.name}: stable html changed`);
			lastStable = r.state.html;
			state = r.state;
		}
		const done = CobMd.renderStream(state, c.input, { sources: c.sources, final: true });
		assert.equal(done.html, CobMd.render(c.input, { sources: c.sources }), c.name);
	}
});

test("chunks put back together are the cleaned text", () => {
	const strip = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g;
	for (const c of FIXTURES.filter((f) => !f.mode)) {
		const { stable, tail } = CobMd._chunks(c.input);
		assert.equal(stable.join("") + tail, c.input.replace(/\r\n?/g, "\n").replace(strip, ""), c.name);
	}
});

test("long and pathological input stays fast", () => {
	const para = "word **bold** and `code` and [link](https://example.com) [1] text. ".repeat(40);
	const big = Array.from({ length: 300 }, () => para).join("\n\n");
	const t0 = Date.now();
	CobMd.render(big, { sources: [1] });
	assert.ok(Date.now() - t0 < 2000, "render took too long");
	const t1 = Date.now();
	CobMd.render("*a ".repeat(3000) + "**b ".repeat(3000) + "_c ".repeat(3000) + "`d ".repeat(1000), { streaming: true });
	assert.ok(Date.now() - t1 < 3000, "pathological markers took too long");
});
