// The widget's freshness sensor (window.CobSensor): what it hashes, when it stays quiet and what it
// sends. Run: node --test tests/widget_js/*.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(
	new URL("../../src/callobuzz_rag/widget/static/sensor.js", import.meta.url),
	"utf8",
);

function load(extra = {}) {
	const ctx = { window: {}, TextEncoder, ...extra };
	ctx.window = ctx.window || {};
	vm.runInNewContext(SOURCE, ctx);
	return ctx.window.CobSensor;
}

function page(parts = {}) {
	const doc = {
		body: { innerText: "BODY text" },
		querySelector(selector) {
			if (selector === 'input[type="password"]') return parts.password ? {} : null;
			if (selector === "main") return parts.main ? { innerText: parts.main } : null;
			if (selector === "article") return parts.article ? { innerText: parts.article } : null;
			return null;
		},
	};
	return doc;
}

function env(over = {}) {
	const calls = [];
	const store = new Map();
	return {
		calls,
		store,
		env: {
			document: page(over.parts),
			location: {
				origin: "https://shreedental.in",
				pathname: "/pricing",
				search: "",
				href: "https://shreedental.in/pricing",
				...over.location,
			},
			localStorage: over.localStorage || {
				getItem: (k) => (store.has(k) ? store.get(k) : null),
				setItem: (k, v) => store.set(k, v),
			},
			random: () => over.random ?? 0,
			now: () => over.now ?? 1_000_000,
			defer: (fn) => fn(),
			fetch: (url, init) => {
				calls.push({ url, init });
				return Promise.resolve({ ok: true });
			},
		},
	};
}

const CFG = { sample: 0.2, skip: ["/login", "/cart", "/blog/*"] };

test("fnv1a64 matches the published test vectors", () => {
	const sensor = load();
	assert.equal(sensor.fnv1a64(""), "cbf29ce484222325");
	assert.equal(sensor.fnv1a64("a"), "af63dc4c8601ec8c");
	assert.match(sensor.fnv1a64("Root canal ka kitna charge hai?"), /^[0-9a-f]{16}$/);
});

test("text comes from main, else article, else body; collapsed, lowercased, capped", () => {
	const sensor = load();
	assert.equal(sensor.mainText(page({ main: "  Root   Canal\n\nRs 4,500 ", article: "x" })), "root canal rs 4,500");
	assert.equal(sensor.mainText(page({ article: "Teeth  Whitening" })), "teeth whitening");
	assert.equal(sensor.mainText(page()), "body text");
	const long = page({ main: "a ".repeat(60000) });
	assert.equal(sensor.mainText(long).length, 50000);
});

test("it reports the address and the code, exactly these two fields", () => {
	const sensor = load();
	const { env: e, calls } = env({ parts: { main: "Pricing Rs 500" } });
	sensor.run("https://rag.example.com", "pk_abc", CFG, e);
	assert.equal(calls.length, 1);
	const { url, init } = calls[0];
	assert.equal(url, "https://rag.example.com/api/v1/widget/page");
	assert.equal(init.method, "POST");
	assert.equal(init.keepalive, true);
	assert.equal(JSON.stringify(init.headers), '{"X-Widget-Key":"pk_abc","Content-Type":"application/json"}');
	const body = JSON.parse(init.body);
	assert.deepEqual(Object.keys(body).sort(), ["fingerprint", "page_url"]);
	assert.equal(body.page_url, "https://shreedental.in/pricing");
	assert.equal(body.fingerprint, sensor.fnv1a64("pricing rs 500"));
	assert.doesNotMatch(init.body, /Pricing Rs/i);
});

test("once per page per browser per 24 hours", () => {
	const sensor = load();
	const world = env({ now: 1_000_000 });
	sensor.run("https://r", "pk", CFG, world.env);
	assert.equal(world.calls.length, 1);
	sensor._reset();
	sensor.run("https://r", "pk", CFG, world.env); // same browser, same page, same day
	assert.equal(world.calls.length, 1);
	sensor._reset();
	world.env.now = () => 1_000_000 + 25 * 60 * 60 * 1000;
	sensor.run("https://r", "pk", CFG, world.env);
	assert.equal(world.calls.length, 2);
});

test("a sample of zero never reports, and only a share of views does", () => {
	const sensor = load();
	const zero = env();
	sensor.run("https://r", "pk", { sample: 0, skip: [] }, zero.env);
	assert.equal(zero.calls.length, 0);
	const unlucky = env({ random: 0.5 });
	sensor.run("https://r", "pk", CFG, unlucky.env);
	assert.equal(unlucky.calls.length, 0);
	sensor.run("https://r", "pk", null, unlucky.env);
	assert.equal(unlucky.calls.length, 0);
});

test("pages with a query string, a password field or a skipped path stay quiet", () => {
	const sensor = load();
	const cases = [
		{ location: { search: "?utm=1", href: "https://shreedental.in/pricing?utm=1" } },
		{ parts: { password: true, main: "x" } },
		{ location: { pathname: "/login" } },
		{ location: { pathname: "/cart/items" } },
		{ location: { pathname: "/blog/new-clinic-opens" } },
	];
	for (const over of cases) {
		sensor._reset();
		const world = env(over);
		sensor.run("https://r", "pk", CFG, world.env);
		assert.equal(world.calls.length, 0, JSON.stringify(over));
	}
});

test("a skip rule matches whole path segments, like the server", () => {
	const sensor = load();
	const rules = { sample: 1, skip: ["/feed", "/account", "/register", "/search"] };
	const skippedPaths = ["/feed", "/feed/x", "/blog/feed", "/Account/orders", "/register"];
	const reportedPaths = ["/feedback", "/accountants", "/registration-fees", "/search-engine-tips"];
	for (const pathname of skippedPaths) {
		sensor._reset();
		const world = env({ location: { pathname, href: "https://shreedental.in" + pathname } });
		sensor.run("https://r", "pk", rules, world.env);
		assert.equal(world.calls.length, 0, pathname);
	}
	for (const pathname of reportedPaths) {
		sensor._reset();
		const world = env({ location: { pathname, href: "https://shreedental.in" + pathname } });
		sensor.run("https://r", "pk", rules, world.env);
		assert.equal(world.calls.length, 1, pathname);
	}
});

test("a throwing localStorage still reports at most once per page load", () => {
	const sensor = load();
	const broken = {
		getItem() {
			throw new Error("blocked");
		},
		setItem() {
			throw new Error("blocked");
		},
	};
	const world = env({ localStorage: broken });
	sensor.run("https://r", "pk", CFG, world.env);
	sensor.run("https://r", "pk", CFG, world.env);
	assert.equal(world.calls.length, 1);
});

test("without BigInt there is no report and no error", () => {
	const sensor = load({ BigInt: undefined });
	const world = env();
	sensor.run("https://r", "pk", CFG, world.env);
	assert.equal(world.calls.length, 0);
});

test("a failing request never throws", async () => {
	const sensor = load();
	const world = env();
	world.env.fetch = () => Promise.reject(new Error("offline"));
	sensor.run("https://r", "pk", CFG, world.env);
	await new Promise((r) => setTimeout(r, 0));
});
