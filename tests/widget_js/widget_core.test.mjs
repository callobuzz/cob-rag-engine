// The widget's pure helpers (window.CobWidgetCore): page globs, greeting frequency.
// Run: node --test tests/widget_js/*.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const here = (p) => new URL(p, import.meta.url);
const source = readFileSync(here("../../src/callobuzz_rag/widget/static/widget-core.js"), "utf8");
const sandbox = { window: {} };
vm.runInNewContext(source, sandbox);
const C = sandbox.window.CobWidgetCore;

function memoryStorage() {
	const m = new Map();
	return {
		getItem: (k) => (m.has(k) ? m.get(k) : null),
		setItem: (k, v) => m.set(k, String(v)),
	};
}
const broken = () => ({
	getItem() {
		throw new Error("denied");
	},
	setItem() {
		throw new Error("denied");
	},
});

test("the page globs agree with the Python function on every shared case", () => {
	const cases = JSON.parse(readFileSync(here("../fixtures/conversation/page_globs.json"), "utf8"));
	assert.ok(cases.length > 20);
	for (const c of cases) {
		assert.equal(C.matchesPage(c.glob, c.url), c.match, `${c.glob} vs ${c.url}`);
	}
});

test("once_per_session uses sessionStorage, once_per_day localStorage by date", () => {
	const g = { id: "home", page_url_glob: "/**", delay_seconds: 5, frequency: "once_per_session" };
	const storage = { session: memoryStorage(), local: memoryStorage() };
	assert.equal(C.proactiveDue(g, "https://x.in/", storage, "2026-10-02T10:00:00Z"), true);
	C.markShown(g, storage, "2026-10-02T10:00:00Z");
	assert.equal(storage.session.getItem("cobrag:pg:home"), "1");
	assert.equal(C.proactiveDue(g, "https://x.in/", storage, "2026-10-02T10:05:00Z"), false);

	const d = { ...g, id: "daily", frequency: "once_per_day" };
	C.markShown(d, storage, "2026-10-02T10:00:00Z");
	assert.equal(storage.local.getItem("cobrag:pg:daily"), "2026-10-02");
	assert.equal(C.proactiveDue(d, "https://x.in/", storage, "2026-10-02T23:00:00Z"), false);
	assert.equal(C.proactiveDue(d, "https://x.in/", storage, "2026-10-03T08:00:00Z"), true);
});

test("always leaves no key and a greeting for another page is not due", () => {
	const g = { id: "tests", page_url_glob: "/tests/**", delay_seconds: 0, frequency: "always" };
	const storage = { session: memoryStorage(), local: memoryStorage() };
	assert.equal(C.proactiveDue(g, "https://x.in/about", storage, "2026-10-02T10:00:00Z"), false);
	assert.equal(C.proactiveDue(g, "https://x.in/tests/a", storage, "2026-10-02T10:00:00Z"), true);
	C.markShown(g, storage, "2026-10-02T10:00:00Z");
	assert.equal(C.proactiveDue(g, "https://x.in/tests/a", storage, "2026-10-02T10:00:01Z"), true);
});

test("dismissed forever never shows again; broken storage does not throw", () => {
	const g = { id: "promo", page_url_glob: "/**", delay_seconds: 0, frequency: "always" };
	const storage = { session: memoryStorage(), local: memoryStorage() };
	C.dismissForever(g, storage);
	assert.equal(storage.local.getItem("cobrag:pg:promo:off"), "1");
	assert.equal(C.proactiveDue(g, "https://x.in/", storage, "2026-10-02T10:00:00Z"), false);
	const b = { session: broken(), local: broken() };
	assert.equal(C.proactiveDue(g, "https://x.in/", b, "2026-10-02T10:00:00Z"), true);
	assert.doesNotThrow(() => C.markShown(g, b, "2026-10-02T10:00:00Z"));
	assert.doesNotThrow(() => C.dismissForever(g, b));
});

test("language: meta, then the assistant's primary, then the browser, then English", () => {
	assert.equal(C.pickLanguage("hi-Latn", "en", "en-IN"), "hi-Latn");
	assert.equal(C.pickLanguage(null, "hi", "en-IN"), "hi");
	assert.equal(C.pickLanguage(null, null, "hi-IN"), "hi");
	assert.equal(C.pickLanguage(null, null, "hi"), "hi");
	assert.equal(C.pickLanguage(null, null, "hindi-ish"), "en"); // not a language tag
	assert.equal(C.pickLanguage("ta", null, "fr"), "en");
	assert.equal(C.pickLanguage("constructor", null, "fr"), "en");
});

test("every language has every string, and the English words are today's words", () => {
	const keys = Object.keys(C.STRINGS.en);
	for (const lang of ["hi", "hi-Latn"]) {
		assert.deepEqual(Object.keys(C.STRINGS[lang]).sort(), [...keys].sort(), lang);
		for (const key of keys) assert.ok(C.STRINGS[lang][key], `${lang}.${key}`);
	}
	assert.equal(C.t("en", "stage_search"), "Searching your documents\u2026");
	assert.equal(C.t("en", "stage_write"), "Writing the answer\u2026");
	assert.equal(C.t("en", "errorStatus", { s: 502 }), "Something went wrong (502)");
	assert.equal(C.t("xx", "send"), "Send"); // an unknown language is English
	assert.equal(C.t("en", "nope"), "nope"); // an unknown key is its own name
});

test("the three defaults are the ones the engine sends", () => {
	assert.deepEqual(Object.keys(C.DEFAULTS_EN).sort(), ["greeting", "placeholder", "title"]);
	for (const key of Object.keys(C.DEFAULTS_EN)) assert.equal(C.STRINGS.en[key], C.DEFAULTS_EN[key]);
});

test("the chrome is impersonal: no first-person feminine verbs in Hindi or Hinglish", () => {
	const text = JSON.stringify([C.STRINGS.hi, C.STRINGS["hi-Latn"]]);
	assert.ok(!/रही हूँ|रहा हूँ|rahi hoon|raha hoon/i.test(text));
});

test("the shared language state is what lead_form.js and handoff.js read", () => {
	assert.equal(C.getLang(), "en");
	C.setLang("hi-Latn");
	assert.equal(C.getLang(), "hi-Latn");
	assert.equal(C.tr("send"), "Bhejein");
	C.setLang("klingon");
	assert.equal(C.getLang(), "en");
});

test("widget.js keeps no visitor-facing English outside the table; Powered-by stays literal", () => {
	const full = readFileSync(here("../../src/callobuzz_rag/widget/static/widget.js"), "utf8");
	// The one-line English fallback (used only when widget-core.js is missing) is allowed.
	const widget = full.replace(/var EN = \{[\s\S]*?\};/, "");
	for (const s of [
		'"Send"',
		"Something went wrong",
		'"Network error"',
		"Start a new conversation",
		'"Close chat"',
		'"Open chat"',
		"Sorry, I couldn't find",
		"Searching your documents",
		"STAGE_WORDS",
	]) {
		assert.equal(widget.includes(s), false, `${s} is still in widget.js`);
	}
	assert.ok(widget.includes('var POWERED_TEXT = "cob-rag-engine by Callobuzz"'));
	assert.equal(widget.includes("POWERED_URL"), false, "the Powered-by line is never a link");
	assert.match(widget, /Noto Sans Devanagari/);
});
