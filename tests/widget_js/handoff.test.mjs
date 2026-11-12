// The widget's handoff card helpers (window.CobHandoff): the card markup, the link allow-list and
// the call-back window. Run: node --test tests/widget_js/*.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function load() {
	const ctx = { window: {} };
	for (const part of ["markdown.js", "handoff.js"]) {
		const source = readFileSync(
			new URL(`../../src/callobuzz_rag/widget/static/${part}`, import.meta.url),
			"utf8",
		);
		vm.runInNewContext(source, ctx);
	}
	return ctx.window.CobHandoff;
}

const block = {
	kind: "handoff_card",
	open_now: false,
	message: "We're closed now. **WhatsApp** us <script>alert(1)</script>",
	reason: "keyword",
	channels: [
		{ kind: "whatsapp", label: "WhatsApp karein", url: "https://wa.me/919812345678?text=Hi%21%20refund" },
		{ kind: "callback", label: "Mujhe call karein", url: null },
		{ kind: "email", label: "Email us", url: "javascript:alert(1)" },
		{ kind: "call", label: "Call <b>us</b>", url: "tel:+919876543210" },
	],
};

test("a card shows channels as links and a callback as a button", () => {
	const html = load().cardHtml(block);
	assert.match(
		html,
		/<a class="chan" href="https:\/\/wa\.me\/919812345678\?text=Hi%21%20refund" target="_blank" rel="noopener noreferrer" data-kind="whatsapp">WhatsApp karein<\/a>/,
	);
	assert.match(html, /<button class="chan" type="button" data-kind="callback">Mujhe call karein<\/button>/);
	assert.match(html, /href="tel:\+919876543210"/);
});

test("an unsafe link is dropped and every text is escaped", () => {
	const html = load().cardHtml(block);
	assert.doesNotMatch(html, /javascript:/i);
	assert.doesNotMatch(html, /<script>/);
	assert.doesNotMatch(html, /<b>us<\/b>/);
	assert.match(html, /&lt;script&gt;/);
	assert.match(html, /Call &lt;b&gt;us&lt;\/b&gt;/);
});

test("the message is rendered as inline Markdown", () => {
	assert.match(load().cardHtml(block), /<strong>WhatsApp<\/strong>/);
});

test("the group is named in the visitor's language and by office hours", () => {
	const h = load();
	assert.match(h.cardHtml({ ...block, open_now: true }), /aria-label="Talk to our team"/);
	assert.match(h.cardHtml(block), /aria-label="Leave us a message"/);
	assert.match(h.cardHtml({ ...block, open_now: true }, "hi-Latn"), /aria-label="Hamari team se baat karein"/);
});

test("isSafeHref allows https, tel and mailto only", () => {
	const h = load();
	assert.equal(h.isSafeHref("https://wa.me/919812345678"), true);
	assert.equal(h.isSafeHref("tel:+919876543210"), true);
	assert.equal(h.isSafeHref("mailto:help@shree-labs.in?subject=x"), true);
	assert.equal(h.isSafeHref(" JaVaScRiPt:alert(1)"), false);
	assert.equal(h.isSafeHref("java\nscript:alert(1)"), false);
	assert.equal(h.isSafeHref("data:text/html,x"), false);
	assert.equal(h.isSafeHref("http://example.com"), false);
	assert.equal(h.isSafeHref(""), false);
	assert.equal(h.isSafeHref(null), false);
});

test("a card with nothing usable renders an empty list, not a broken link", () => {
	const html = load().cardHtml({ open_now: true, message: "Hi", channels: [{ kind: "email", label: "x", url: "ftp://x" }] });
	assert.doesNotMatch(html, /<a /);
});

test("callbackWindow reads the day and the chosen slot", () => {
	const h = load();
	// (the helper lives in another realm: compare plain data)
	assert.deepEqual(JSON.parse(JSON.stringify(h.callbackWindow("2026-10-06", "16:00-18:00"))), {
		date: "2026-10-06",
		start: "16:00",
		end: "18:00",
	});
	assert.equal(h.callbackWindow("2026-10-06", "18:00-16:00"), null);
	assert.equal(h.callbackWindow("", "16:00-18:00"), null);
	assert.equal(h.callbackWindow("2026-10-06", ""), null);
	assert.equal(h.callbackWindow("06/10/2026", "16:00-18:00"), null);
	assert.equal(h.callbackWindow("2026-10-06", "4pm"), null);
});

// TV2 reference
test("a card with a reference shows it bold and an unknown ref_code field is harmless", () => {
	const withRef = {
		...block,
		open_now: true,
		ref_code: "SD-4F7K",
		message: "Our team is around.\n\nYour reference: **SD-4F7K** — quote it if you call.",
	};
	const html = load().cardHtml(withRef);
	assert.match(html, /<strong>SD-4F7K<\/strong>/);
	assert.doesNotMatch(html, /undefined|null/);
	// the same card without the new field renders the same channels
	const { ref_code: _ignored, ...legacy } = withRef;
	assert.equal(load().cardHtml(legacy).includes('data-kind="whatsapp"'), true);
});
// /TV2 reference
