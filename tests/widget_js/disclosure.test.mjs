// TV5: the AI badge and first-line disclosure. The badge markup is pure (widget-core.js); widget.js
// only wires it into the header and puts the first line above the greeting.
// Run: node --test tests/widget_js/*.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const read = (name) =>
	readFileSync(new URL(`../../src/callobuzz_rag/widget/static/${name}`, import.meta.url), "utf8");
const sandbox = { window: {} };
vm.runInNewContext(read("widget-core.js"), sandbox);
const C = sandbox.window.CobWidgetCore;
const widget = read("widget.js");

test("the badge is a note with text in the visitor's language", () => {
	assert.match(C.aiBadgeHtml(true, "en"), /^<span class="ai-badge" role="note">AI assistant<\/span>$/);
	assert.match(C.aiBadgeHtml(true, "hi"), />एआई सहायक</);
	assert.match(C.aiBadgeHtml(true, "hi-Latn"), />AI assistant</);
	assert.match(C.aiBadgeHtml(true, "klingon"), />AI assistant</);
});

test("nothing renders when the config has no badge", () => {
	assert.equal(C.aiBadgeHtml(false, "en"), "");
	assert.equal(C.aiBadgeHtml(null, "en"), "");
	assert.equal(C.aiBadgeHtml(undefined, "en"), "");
});

test("the badge is never only a colour: it carries words and no click handler", () => {
	const html = C.aiBadgeHtml(true, "en");
	assert.doesNotMatch(html, /onclick|href=|<script/);
});

test("the three languages have a badge string", () => {
	for (const lang of ["en", "hi", "hi-Latn"]) assert.ok(C.STRINGS[lang].aiBadge, lang);
});

test("widget.js reads the badge and the line from the config and shows them", () => {
	assert.match(widget, /ai_badge: null/);
	assert.match(widget, /disclosure_line: null/);
	assert.match(widget, /CORE\.aiBadgeHtml\(!!cfg\.ai_badge,/);
	assert.match(widget, /\/\/ TV5 disclosure/);
});

test("the first line is the first bot message, above the greeting, every time the chat is drawn", () => {
	const render = /function renderAll\(\) \{[\s\S]*?\n  \}\n/.exec(widget)[0];
	const line = render.indexOf("cfg.disclosure_line");
	const greeting = render.indexOf('label("greeting")');
	assert.ok(line > 0 && greeting > line, "disclosure line is drawn before the greeting");
	// drawn through the markdown writer, which escapes: an owner's <script> shows as text
	assert.match(render, /mdInto\(bubble\("bot disclosure", ""\), discLine/);
	// in the visitor's language when the engine sent one, else the primary line
	assert.match(render, /\(cfg\.disclosure_lines \|\| \{\}\)\[visitorLang\(\)\] \|\| cfg\.disclosure_line/);
});

test("with a badge the old header sub-line is not also shown", () => {
	assert.match(widget, /var disclosure = cfg\.ai_badge\s+\?[\s\S]*?: cfg\.ai_disclosure/);
});

// TV5 freshness: "Updated 3 Oct" on a source chip
test("a date reads as Updated 3 Oct, in the visitor's language", () => {
	assert.equal(C.updatedLabel("2026-10-03", "en"), "Updated 3 Oct");
	assert.equal(C.updatedLabel("2026-09-12", "hi-Latn"), "Update: 12 Sep");
	assert.match(C.updatedLabel("2026-10-03", "hi"), /^अपडेट: 3 /);
	assert.equal(C.updatedLabel("2026-01-05", "klingon"), "Updated 5 Jan");
});

test("anything that is not a date gives no label", () => {
	for (const bad of [null, undefined, "", "yesterday", "2026-13-01", "2026-10-3", "<b>x</b>"]) {
		assert.equal(C.updatedLabel(bad, "en"), "");
	}
});

test("widget.js shows the date on chips and keeps it when it slims the sources", () => {
	assert.match(widget, /CORE\.updatedLabel/);
	assert.match(widget, /updated_at: s\.updated_at/);
	assert.match(widget, /\+ updatedPart\(s\)/);
});
