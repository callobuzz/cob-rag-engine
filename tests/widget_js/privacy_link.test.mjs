// P3: the widget footer shows a "Privacy" link only when the engine sent an http(s) address.
// Run: node --test tests/widget_js/*.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const read = (name) =>
	readFileSync(new URL(`../../src/callobuzz_rag/widget/static/${name}`, import.meta.url), "utf8");
const widget = read("widget.js");
const core = { window: {} };
vm.runInNewContext(read("widget-core.js"), core);
const C = core.window.CobWidgetCore;

function slice(name) {
	const start = widget.indexOf(`function ${name}(`);
	assert.ok(start >= 0, `${name} is in widget.js`);
	const open = widget.indexOf("{", start);
	let depth = 0;
	for (let i = open; i < widget.length; i++) {
		if (widget[i] === "{") depth++;
		else if (widget[i] === "}" && --depth === 0) return widget.slice(start, i + 1);
	}
	throw new Error(`unbalanced ${name}`);
}

function footFor(cfg, lang = "en") {
	const sandbox = { cfg, t: (key) => C.STRINGS[lang][key] };
	vm.runInNewContext(`${slice("esc")}\n${slice("footHtml")}\nresult = footHtml();`, sandbox);
	return sandbox.result;
}

const LINE = "cob-rag-engine by Callobuzz";

test("the three languages have a privacy string", () => {
	assert.equal(C.STRINGS.en.privacy, "Privacy");
	assert.equal(C.STRINGS.hi.privacy, "गोपनीयता");
	assert.equal(C.STRINGS["hi-Latn"].privacy, "Privacy");
});

test("without an address the footer is exactly what it was", () => {
	const html = footFor({ powered_by: { text: LINE }, privacy_url: null });
	assert.equal(html, `<div class="foot"><span class="powered">${LINE}</span></div>`);
});

test("an address adds a link that opens in a new tab; the address is set on the element, not in markup", () => {
	const html = footFor({
		powered_by: { text: LINE },
		privacy_url: "https://sahayak.example/legal/privacy",
	});
	assert.match(html, /<span class="powered">cob-rag-engine by Callobuzz<\/span> · <a class="privacy"/);
	assert.match(html, /target="_blank" rel="noopener">Privacy<\/a>/);
	assert.doesNotMatch(html, /href/);
});

function wire(cfg, anchor) {
	const root = { querySelector: (sel) => (sel === ".foot a.privacy" ? anchor : null) };
	vm.runInNewContext(`${slice("wirePrivacy")}
wirePrivacy(root);`, { cfg, root });
}

test("wirePrivacy sets the href of the drawn link, only for http(s)", () => {
	const set = [];
	const anchor = { setAttribute: (k, v) => set.push([k, v]) };
	wire({ privacy_url: "https://sahayak.example/legal/privacy" }, anchor);
	assert.deepEqual(set, [["href", "https://sahayak.example/legal/privacy"]]);
	set.length = 0;
	wire({ privacy_url: "javascript:alert(1)" }, anchor);
	wire({ privacy_url: null }, anchor);
	wire({ privacy_url: "https://x.example" }, null); // no link drawn: nothing to do
	assert.deepEqual(set, []);
});

test("the link text follows the visitor's language", () => {
	const html = footFor(
		{ powered_by: null, privacy_url: "https://shree-labs.in/p" },
		"hi",
	);
	assert.match(html, />गोपनीयता<\/a>/);
	assert.doesNotMatch(html, /powered/);
});

test("only http(s) addresses become a link", () => {
	for (const bad of ["javascript:alert(1)", "data:text/html,x", "//evil.example/x", "/relative"]) {
		const html = footFor({ powered_by: null, privacy_url: bad });
		assert.equal(html, "", bad);
	}
});

test("widget.js reads privacy_url from the config", () => {
	assert.match(widget, /privacy_url: null/);
});
