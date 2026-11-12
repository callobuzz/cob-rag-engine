// TV1: the "Talk to a person" button in the widget header. Its markup and its send-once queue
// live in widget-core.js (pure, no DOM); widget.js only wires them to the panel.
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

// What the header would ask, read from widget.js itself (the same table the call-to-action uses).
const ASKS = vm.runInNewContext(
	"(" + /var HANDOFF_ASKS = (\{[\s\S]*?\});/.exec(widget)[1] + ")",
	{},
);

test("the button is labelled in the visitor's language", () => {
	assert.match(C.humanButtonHtml(true, "en"), />Talk to a person</);
	assert.match(C.humanButtonHtml(true, "hi"), />किसी व्यक्ति से बात करें</);
	assert.match(C.humanButtonHtml(true, "hi-Latn"), />Kisi insaan se baat karein</);
	assert.match(C.humanButtonHtml(true, "klingon"), />Talk to a person</);
});

test("human_button false means no button at all", () => {
	assert.equal(C.humanButtonHtml(false, "en"), "");
	assert.equal(C.humanButtonHtml(undefined, "en"), "");
});

test("the button is a real button with a name, reachable by keyboard, and never a link", () => {
	const html = C.humanButtonHtml(true, "en");
	assert.match(html, /^<button class="human" type="button" /);
	assert.match(html, /aria-label="Talk to a person"/);
	assert.doesNotMatch(html, /href=|<a |tabindex="-1"|onclick/);
});

test("a tap while idle sends exactly the visitor's ask, once", () => {
	const sent = [];
	let busy = false;
	const ctl = C.humanAsk(() => busy, (reason) => sent.push(reason));
	ctl.tap();
	assert.equal(sent.length, 1);
	ctl.done(); // nothing queued: nothing more is sent
	assert.equal(sent.length, 1);
});

test("a tap while a reply streams waits for it to end and is never sent twice", () => {
	const sent = [];
	let busy = true;
	const ctl = C.humanAsk(() => busy, () => sent.push(1));
	ctl.tap();
	ctl.tap(); // a second tap changes nothing
	assert.equal(sent.length, 0);
	ctl.done(); // still streaming
	assert.equal(sent.length, 0);
	busy = false;
	ctl.done();
	assert.equal(sent.length, 1);
	ctl.done();
	assert.equal(sent.length, 1);
});

test("widget.js wires the header button to the same ask as the call-to-action, and the config flag", () => {
	assert.match(widget, /human_button: false/); // the config default
	assert.match(widget, /CORE\.humanButtonHtml\(cfg\.human_button,/);
	assert.match(widget, /CORE\.humanAsk\(/);
	assert.match(widget, /HANDOFF_ASKS\[visitorLang\(\)\]/);
	assert.match(widget, /\/\/ TV1 human/);
});

test("each ask the button can send is a request for a person in its own language", () => {
	assert.deepEqual(Object.keys(ASKS).sort(), ["en", "hi", "hi-Latn"]);
});

test("the chrome strings exist in every language and stay impersonal", () => {
	for (const lang of ["en", "hi", "hi-Latn"]) {
		assert.ok(C.STRINGS[lang].talkToPerson, lang);
	}
	assert.ok(!/रही हूँ|रहा हूँ|rahi hoon|raha hoon/i.test(JSON.stringify(C.STRINGS.hi)));
});

test("a used-up handoff bucket reads as a polite reply, never a red error", () => {
	assert.match(widget, /POLITE_CODES = \[[^\]]*"handoff_rate_limited"/);
});
