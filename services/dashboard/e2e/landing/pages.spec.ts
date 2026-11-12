// LP3: pricing, features, about and contact in a real browser, on snapshots rendered by the
// engine's own renderer (no database, no stack). It brings its own snapshot folder and server so it
// does not depend on the home page's specs. Screenshots go to LANDING_SHOTS_DIR (default: the OS
// temp folder), never the repository.

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { startServer } from "./serve.mjs";

const PORT = 4311; // each worker uses the next port and its own folder, so two can run side by side
const BRANDS = ["#0f766e", "#e11d48", "#facc15"] as const;
const repo = path.resolve(import.meta.dirname, "../../../..");
const base = path.join(process.env.LANDING_SNAP_DIR || os.tmpdir(), "cob-landing-pages");
let root = base;
let port = PORT;
const shots = process.env.LANDING_SHOTS_DIR || path.join(os.tmpdir(), "cob-landing-shots");
const python =
	process.env.LANDING_PY ||
	(process.platform === "win32"
		? path.join(repo, ".venv", "Scripts", "python.exe")
		: path.join(repo, ".venv", "bin", "python"));

/** The example plans' ids: uuid5 in the DNS namespace of "<name>.sharmadental.in" (snapshot.py). */
function planId(name: string): string {
	const ns = Buffer.from("6ba7b8109dad11d180b400c04fd430c8", "hex");
	const h = createHash("sha1")
		.update(Buffer.concat([ns, Buffer.from(`${name}.sharmadental.in`)]))
		.digest();
	h[6] = ((h[6] as number) & 0x0f) | 0x50;
	h[8] = ((h[8] as number) & 0x3f) | 0x80;
	const hex = h.subarray(0, 16).toString("hex");
	return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function snapshot(name: string, args: string[]): void {
	const out = path.join(root, name);
	fs.rmSync(out, { recursive: true, force: true });
	execFileSync(python, ["-m", "callobuzz_rag.landing.snapshot", ...args, "--out", out], {
		cwd: repo,
		env: { ...process.env, PYTHONIOENCODING: "utf-8" },
		stdio: ["ignore", "ignore", "inherit"],
	});
}

let server: import("node:http").Server;

test.beforeAll(async ({ browserName: _b }, testInfo) => {
	root = `${base}-${testInfo.parallelIndex}`;
	port = PORT + testInfo.parallelIndex;
	fs.mkdirSync(root, { recursive: true });
	fs.mkdirSync(shots, { recursive: true });
	for (const brand of BRANDS) {
		snapshot(`b${brand.slice(1)}`, ["--content", "default", "--brand", brand, "--base", "auto", "--lang", "en"]);
	}
	// A site whose first card has a dollar plan and a yearly plan, so both toggles appear.
	const content = path.join(root, "toggles.json");
	const dump = execFileSync(
		python,
		["-c", "from callobuzz_rag.landing.schema import default_content as d; print(d().model_dump_json())"],
		{ cwd: repo, env: { ...process.env, PYTHONIOENCODING: "utf-8" }, encoding: "utf-8" },
	);
	const data = JSON.parse(dump);
	data.pricing.cards = [
		{
			who_for: { en: "For a clinic" },
			highlight: { en: "Most chosen" },
			variants: {
				INR: { monthly: planId("Standard"), yearly: planId("Standard yearly") },
				USD: { monthly: planId("Standard USD") },
			},
		},
		{ who_for: { en: "For a group" }, variants: { INR: { monthly: planId("Pro") } } },
	];
	fs.writeFileSync(content, JSON.stringify(data));
	snapshot("toggles", ["--content", content, "--base", "auto", "--lang", "en"]);
	server = await startServer(root, path.join(root, `b${BRANDS[0].slice(1)}`), port);
});

test.afterAll(async () => {
	await new Promise<void>((done) => server?.close(() => done()) ?? done());
});

const url = (site: string, page: string): string => `http://127.0.0.1:${port}/${site}/${page}`;

async function noSideScroll(page: Page): Promise<void> {
	const widths = await page.evaluate(() => ({
		scroll: document.documentElement.scrollWidth,
		client: document.documentElement.clientWidth,
	}));
	expect(widths.scroll).toBeLessThanOrEqual(widths.client);
}

test.describe("pricing", () => {
	for (const brand of BRANDS) {
		test(`looks right and has no side scroll or serious accessibility problem (${brand})`, async ({ page }, info) => {
			await page.goto(url(`b${brand.slice(1)}`, "pricing/"));
			await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
			await noSideScroll(page);
			await page.screenshot({
				path: path.join(shots, `pricing-${brand.slice(1)}-${info.project.name}.png`),
				fullPage: true,
			});
			const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
			const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
			expect(bad.map((v) => `${v.id}: ${v.nodes[0]?.html}`)).toEqual([]);
		});
	}

	test("the slider and the number field work by keyboard and the result says estimated", async ({ page }) => {
		await page.goto(url("b0f766e", "pricing/"));
		const result = page.locator("[data-calc-result]");
		await expect(result).toContainText("Estimated cost");
		const before = await result.textContent();
		const range = page.locator("[data-calc-range]");
		await range.focus();
		await page.keyboard.press("End");
		await expect(page.locator("[data-calc-number]")).toHaveValue("5000");
		await expect(result).not.toHaveText(before ?? "");
		await expect(result).toContainText("Estimated cost");
		await page.locator("[data-calc-number]").fill("50");
		await expect(result).toContainText("Free fits best");
	});

	test("the toggles swap the prices without a reload, by keyboard", async ({ page }) => {
		await page.goto(url("toggles", "pricing/"));
		const card = page.locator("[data-card]").first();
		await expect(card.locator("[data-variant]:not([hidden]) .plan-amount")).toHaveText("₹999");
		const yearly = page.locator('[data-toggle="period"][data-value="yearly"]');
		await yearly.focus();
		await page.keyboard.press("Enter");
		await expect(card.locator("[data-variant]:not([hidden]) .plan-amount")).toHaveText("₹825");
		await expect(card.locator("[data-variant]:not([hidden])")).toContainText("billed ₹9,900 yearly");
		await expect(page).toHaveURL(/pricing\/$/);
		await page.locator('[data-toggle="currency"][data-value="USD"]').click();
		await expect(card.locator("[data-variant]:not([hidden]) .plan-amount")).toHaveText("$15");
		await noSideScroll(page);
	});

	test.describe("without JavaScript", () => {
		test.use({ javaScriptEnabled: false });
		test("every plan is readable and the toggles are links", async ({ page }) => {
			await page.goto(url("toggles", "pricing/"));
			await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
			const link = page.locator('[data-toggle="currency"][data-value="USD"]');
			await expect(link).toHaveAttribute("href", /currency=USD/);
			await expect(page.locator("[data-calc-result]")).toContainText("Estimated cost");
		});
	});
});

test.describe("features, about and contact", () => {
	for (const pageName of ["features", "about", "contact"]) {
		test(`${pageName} has no side scroll and no serious accessibility problem`, async ({ page }, info) => {
			await page.goto(url("b0f766e", `${pageName}/`));
			await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
			await noSideScroll(page);
			await page.screenshot({ path: path.join(shots, `${pageName}-${info.project.name}.png`), fullPage: true });
			const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
			const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
			expect(bad.map((v) => `${v.id}: ${v.nodes[0]?.html}`)).toEqual([]);
		});
	}

	test("the contact form checks the fields, then posts JSON and thanks the visitor", async ({ page }) => {
		await page.route("**/api/v1/public/landing/contact", async (route) => {
			const body = route.request().postDataJSON();
			expect(body).toMatchObject({ name: "Priya Sharma", consent: true, topic: "sales", website: "" });
			await route.fulfill({ status: 202, contentType: "application/json", body: '{"ok":true}' });
		});
		await page.goto(url("b0f766e", "contact/"));
		await page.getByRole("button", { name: "Send message" }).click();
		await expect(page.locator(".field-error").first()).toBeVisible();
		await page.getByLabel("Your name").fill("Priya Sharma");
		await page.getByLabel("E-mail").fill("priya@sharmadental.in");
		await page.getByLabel("Your message").fill("We run two clinics in Pune. Can it answer in Marathi?");
		await page.getByRole("checkbox").check();
		await page.getByRole("button", { name: "Send message" }).click();
		await expect(page.locator("form .form-status")).toContainText("Thank you");
	});

	test.describe("without JavaScript", () => {
		test.use({ javaScriptEnabled: false });
		test("the form is a plain post to /contact", async ({ page }) => {
			await page.goto(url("b0f766e", "contact/"));
			const form = page.locator("form.contact-form");
			await expect(form).toHaveAttribute("method", "post");
			await expect(form).toHaveAttribute("action", "/contact");
			await expect(form.locator('input[name="website"]')).toBeAttached();
		});
	});
});
