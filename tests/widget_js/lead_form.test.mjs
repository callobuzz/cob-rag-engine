// The widget's lead form helpers (window.CobLeadForm): validation that mirrors the server's, the
// messages in three languages, and the call-back request. Run: node --test tests/widget_js/*.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(
	new URL("../../src/callobuzz_rag/widget/static/lead_form.js", import.meta.url),
	"utf8",
);
const ctx = { window: {} };
vm.runInNewContext(source, ctx);
const F = ctx.window.CobLeadForm;

test("Indian mobile numbers normalise like the server", () => {
	assert.equal(F.validatePhone("098765 43210").e164, "+919876543210");
	assert.equal(F.validatePhone("+91 98765-43210").e164, "+919876543210");
	assert.equal(F.validatePhone("91 9876543210").e164, "+919876543210");
	assert.equal(F.validatePhone("(+91) 6123456789").e164, "+916123456789");
	assert.equal(F.validatePhone("+971 50 123 4567").e164, "+971501234567");
	assert.equal(F.validatePhone("+44 7911 123456").e164, "+447911123456");
});

test("what the server refuses, the form refuses", () => {
	for (const bad of ["5876543210", "98765", "98765432101", "abc", "+0 123", "+91 5876543210", ""]) {
		assert.equal(F.validatePhone(bad).ok, false, bad);
	}
});

test("e-mail check is syntax only", () => {
	assert.equal(F.validateEmail(" priya@shree-labs.in "), true);
	assert.equal(F.validateEmail("priya@"), false);
	assert.equal(F.validateEmail("priya shree@x.in"), false);
});

test("collect reports required and format errors in the visitor's language", () => {
	const fields = [
		{ id: "name", type: "text", required: false },
		{ id: "phone", type: "tel", required: true },
		{ id: "email", type: "email", required: false },
	];
	const bad = F.collect(fields, { phone: "123", email: "x@" }, "hi");
	assert.equal(bad.errors.phone, "10 अंकों का मोबाइल नंबर लिखें।");
	assert.equal(bad.errors.email, "सही ई-मेल लिखें।");
	const good = F.collect(fields, { name: " Priya ", phone: "9876543210" }, "en");
	assert.equal(JSON.stringify(good.errors), "{}");
	assert.equal(JSON.stringify(good.payload), JSON.stringify({ name: "Priya", phone: "9876543210" }));
	assert.equal(F.collect(fields, {}, "hi-Latn").errors.phone, "Kripya yeh bhariye.");
	assert.equal(F.collect(fields, {}, "ta").errors.phone, "Please fill this in.");
});

test("number and date fields are checked, select values pass through", () => {
	const fields = [
		{ id: "members", type: "number", required: false },
		{ id: "when", type: "date", required: false },
		{ id: "test", type: "select", required: true },
	];
	const bad = F.collect(fields, { members: "two", when: "15/10", test: "" }, "en");
	assert.equal(Object.keys(bad.errors).sort().join(), "members,test,when");
	const good = F.collect(fields, { members: "2", when: "2026-10-15", test: "HbA1c" }, "en");
	assert.equal(JSON.stringify(good.payload), JSON.stringify({ members: "2", when: "2026-10-15", test: "HbA1c" }));
});

test("every language has every message", () => {
	const keys = Object.keys(F.messages("en")).sort();
	for (const lang of ["hi", "hi-Latn"]) {
		assert.equal(Object.keys(F.messages(lang)).sort().join(), keys.join(), lang);
	}
});

test("the call-back request is in the visitor's language", () => {
	assert.equal(F.callbackMessage("en"), "I'd like a call back");
	assert.equal(F.callbackMessage("hi-Latn"), "Mujhe call back chahiye");
	assert.equal(F.callbackMessage("ta"), "I'd like a call back");
});

test("lead_form.js stays ES5 and dependency-free", () => {
	const banned = [/\beval\s*\(/, /new Function/, /\bimport\b/, /\brequire\s*\(/, /=>/, /\blet\b/, /\bconst\b/, /innerHTML/];
	for (const bad of banned) assert.doesNotMatch(source, bad, String(bad));
});

test("every language has a message for a rate-limited submission", () => {
	for (const lang of ["en", "hi", "hi-Latn"]) {
		const m = F.messages(lang);
		assert.equal(typeof m.tooMany, "string", lang);
		assert.ok(m.tooMany.length > 0 && m.tooMany !== m.failed, lang);
	}
	assert.match(F.messages("en").tooMany, /Too many tries/);
});
