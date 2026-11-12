import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
	appearanceSchema,
	appearanceToBody,
	appearanceToForm,
	checkOrigin,
	createSchema,
	previewAllowed,
	sitesSentence,
	splitOrigins,
	whiteTextContrast,
} from "./schemas";

describe("checkOrigin", () => {
	it("accepts https sites and normalises them like the engine", () => {
		expect(checkOrigin("https://Shree-Labs.in/")).toEqual({ ok: true, origin: "https://shree-labs.in" });
		expect(checkOrigin("  https://www.shree-labs.in  ")).toEqual({ ok: true, origin: "https://www.shree-labs.in" });
		expect(checkOrigin("https://shree-labs.in:8443")).toEqual({ ok: true, origin: "https://shree-labs.in:8443" });
	});

	it("allows plain http only for localhost and 127.0.0.1", () => {
		expect(checkOrigin("http://localhost:5173")).toEqual({ ok: true, origin: "http://localhost:5173" });
		expect(checkOrigin("http://127.0.0.1:8090")).toEqual({ ok: true, origin: "http://127.0.0.1:8090" });
		const r = checkOrigin("http://shree-labs.in");
		expect(r.ok).toBe(false);
		expect(!r.ok && r.error).toMatch(/Plain http:\/\/ is allowed only for localhost/);
	});

	it("refuses paths, queries, credentials and non-web schemes", () => {
		for (const bad of [
			"https://shree-labs.in/help",
			"https://shree-labs.in?x=1",
			"https://shree-labs.in#top",
			"https://user@shree-labs.in",
			"ftp://shree-labs.in",
			"shree-labs.in",
			"",
		]) {
			expect(checkOrigin(bad).ok, bad).toBe(false);
		}
	});

	it("accepts * with a warning and refuses duplicates", () => {
		const any = checkOrigin("*");
		expect(any).toMatchObject({ ok: true, origin: "*" });
		expect(any.ok && any.warning).toMatch(/any website can use this key/i);
		expect(checkOrigin("*", ["*"]).ok).toBe(false);
		const dup = checkOrigin("https://SHREE-labs.in", ["https://shree-labs.in"]);
		expect(dup.ok).toBe(false);
		expect(!dup.ok && dup.error).toBe("https://shree-labs.in is already in the list.");
	});

	it("splits pasted lists on lines, commas and spaces", () => {
		expect(splitOrigins("https://a.in, https://b.in\nhttps://c.in  ")).toEqual([
			"https://a.in",
			"https://b.in",
			"https://c.in",
		]);
	});
});

describe("form bounds", () => {
	const valid = {
		name: "Shree Labs site",
		allowed_origins: ["https://shree-labs.in"],
		rate_limit_per_minute: 10,
		daily_message_limit: 1000,
		appearance: appearanceToForm({}),
	};
	const issues = (over: Record<string, unknown>) => {
		const r = createSchema.safeParse({ ...valid, ...over });
		return r.success ? [] : r.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`);
	};

	it("accepts the defaults", () => {
		expect(issues({})).toEqual([]);
		expect(issues({ daily_message_limit: 0, rate_limit_per_minute: 600 })).toEqual([]);
		expect(issues({ daily_message_limit: 10_000_000, rate_limit_per_minute: 1 })).toEqual([]);
	});

	it("rejects out-of-range limits, an empty name and no sites", () => {
		expect(issues({ rate_limit_per_minute: 0 })).toEqual(["rate_limit_per_minute: Use a whole number from 1 to 600."]);
		expect(issues({ rate_limit_per_minute: 601 })).toHaveLength(1);
		expect(issues({ rate_limit_per_minute: 2.5 })).toHaveLength(1);
		expect(issues({ daily_message_limit: -1 })).toHaveLength(1);
		expect(issues({ daily_message_limit: 10_000_001 })).toEqual([
			"daily_message_limit: Use a whole number from 0 to 1,00,00,000.",
		]);
		expect(issues({ name: "  " })).toEqual(["name: Enter a name."]);
		expect(issues({ name: "x".repeat(256) })).toEqual(["name: Use at most 255 characters."]);
		expect(issues({ allowed_origins: [] })).toEqual(["allowed_origins: Add at least one site."]);
		expect(issues({ allowed_origins: Array.from({ length: 51 }, (_, i) => `https://s${i}.in`) })).toHaveLength(1);
	});

	it("appearance: lengths and #RRGGBB", () => {
		const a = (over: Record<string, unknown>) =>
			appearanceSchema.safeParse({ ...appearanceToForm({}), ...over }).success;
		expect(a({ title: "x".repeat(80), greeting: "x".repeat(500), placeholder: "x".repeat(120) })).toBe(true);
		expect(a({ title: "x".repeat(81) })).toBe(false);
		expect(a({ greeting: "x".repeat(501) })).toBe(false);
		expect(a({ placeholder: "x".repeat(121) })).toBe(false);
		expect(a({ primary_color: "#0b7a5f" })).toBe(true);
		expect(a({ primary_color: "" })).toBe(true);
		expect(a({ primary_color: "#0b7a5" })).toBe(false);
		expect(a({ primary_color: "teal" })).toBe(false);
	});
});

describe("appearance bodies", () => {
	it("a create sends only what is set; a patch sends null to clear", () => {
		const f = { ...appearanceToForm({}), title: " Shree Labs help ", primary_color: "#0b7a5f" };
		expect(appearanceToBody(f, false)).toEqual({
			title: "Shree Labs help",
			primary_color: "#0b7a5f",
			position: "right",
			show_sources: true,
		});
		expect(appearanceToBody(f, true)).toEqual({
			title: "Shree Labs help",
			greeting: null,
			placeholder: null,
			primary_color: "#0b7a5f",
			position: "right",
			show_sources: true,
		});
	});
});

describe("helpers", () => {
	it("contrast of white text on the brand colour", () => {
		expect(whiteTextContrast("#0b7a5f").ok).toBe(true);
		expect(whiteTextContrast("#3fd4a8").ok).toBe(false);
	});

	it("sites sentence and preview origin", () => {
		expect(sitesSentence(["https://shree-labs.in"])).toBe("shree-labs.in");
		expect(sitesSentence(["https://shree-labs.in", "https://www.shree-labs.in", "http://localhost:5173"])).toBe(
			"shree-labs.in, www.shree-labs.in and localhost:5173",
		);
		expect(sitesSentence(["*"])).toBe("all its sites");
		expect(previewAllowed(["https://shree-labs.in"], "http://127.0.0.1:5173")).toBe(false);
		expect(previewAllowed(["https://shree-labs.in", "http://127.0.0.1:5173"], "http://127.0.0.1:5173")).toBe(true);
		expect(previewAllowed(["*"], "http://127.0.0.1:5173")).toBe(true);
	});
});

describe("widget-demo.html", () => {
	const file = readFileSync(path.resolve(import.meta.dirname, "../../../public/widget-demo.html"), "utf8");

	it("is an Indian sample site with nothing US-specific", () => {
		for (const banned of ["USA", "$", "ZIP", "Summit"]) expect(file.includes(banned), banned).toBe(false);
		expect(file).toContain("Anand Dental Care");
		expect(file).toContain("Kothrud");
		expect(file).toContain("Book on WhatsApp");
		expect(file).toContain("+91");
	});

	it("keeps the ?key=&api= contract and opens the widget", () => {
		const loader = readFileSync(path.resolve(import.meta.dirname, "../../../public/widget-demo.js"), "utf8");
		expect(file).toContain('<script src="widget-demo.js"></script>');
		expect(loader).toContain('params.get("key")');
		expect(loader).toContain('params.get("api")');
		expect(loader).toContain('s.setAttribute("data-open", "true")');
		expect(loader).toContain('"/widget.js"');
	});

	it("has no inline script, so the image's CSP needs no 'unsafe-inline' (D12)", () => {
		expect(file).not.toMatch(/<script(?![^>]*\ssrc=)[^>]*>/);
	});
});
