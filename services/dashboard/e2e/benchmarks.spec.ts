import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * The Benchmarks page and the pickers beside the model fields, on the mock engine serving the
 * engine's real result files (src/test/msw/handlers/*.fixture.json). Across the README matrix:
 * no sideways scroll, no console errors, axe clean, and a screenshot of each tab.
 */

const PATIENT = "7b221003-0000-4000-8000-000000000001";

async function noSideScroll(page: Page, name: string) {
	const width = page.viewportSize()?.width ?? 1440;
	const m = await page.evaluate(() => ({
		doc: document.documentElement.scrollWidth,
		content: document.getElementById("content")?.scrollWidth ?? 0,
		contentClient: document.getElementById("content")?.clientWidth ?? 0,
	}));
	expect(m.doc, `${name}: document`).toBeLessThanOrEqual(width);
	expect(m.content, `${name}: #content`).toBeLessThanOrEqual(m.contentClient + 1);
}

test.describe("benchmarks", () => {
	let errors: string[] = [];

	test.beforeEach(async ({ context, page }, info) => {
		errors = [];
		page.on("console", (m) => {
			if (m.type() === "error") errors.push(m.text());
		});
		page.on("pageerror", (e) => errors.push(e.message));
		await installMockEngine(context, createWorld());
		await logIn(page, roleOf(info));
	});

	test.afterEach(() => {
		expect(errors).toEqual([]);
	});

	test("the page: recommended setup, three tabs, filters and the method drawer", async ({ page }, info) => {
		await page.goto("/benchmarks");
		const hero = page.getByRole("region", { name: "Recommended setup" });
		await expect(hero.getByText("Gemma 4 31B", { exact: true })).toBeVisible();
		await expect(hero.getByText("GPT-6 Luna", { exact: true })).toBeVisible();
		await expect(hero.getByText("Gemma 4 26B", { exact: true })).toBeVisible();
		const answers = page.getByRole("table", { name: /Answer-model benchmark results: All/ });
		await expect(answers).toBeVisible();
		await noSideScroll(page, "answer models");
		await expectNoAxeViolations(page, "benchmarks answer models");
		await fullShot(page, screenshotPath(info, "benchmarks-answer"));

		await page.getByRole("radio", { name: "Hindi + Hinglish" }).click();
		await expect(page.getByRole("table", { name: /Hindi \+ Hinglish/ })).toBeVisible();
		await noSideScroll(page, "answer models, Hindi");
		await fullShot(page, screenshotPath(info, "benchmarks-answer-hindi"));

		await page.getByRole("button", { name: "Method" }).click();
		const drawer = page.getByRole("dialog", { name: "How this was measured" });
		await expect(drawer.getByText(/94 frozen items/)).toBeVisible();
		await expectNoAxeViolations(page, "benchmarks method drawer");
		await page.screenshot({ path: screenshotPath(info, "benchmarks-method") });
		await page.keyboard.press("Escape");
		await expect(drawer).toBeHidden();

		await page.getByRole("tab", { name: "Fast models" }).click();
		await expect(page.getByRole("table", { name: "Fast-model benchmark results" })).toBeVisible();
		await noSideScroll(page, "fast models");
		await expectNoAxeViolations(page, "benchmarks fast models");
		await fullShot(page, screenshotPath(info, "benchmarks-fast"));

		await page.getByRole("tab", { name: "Retrieval" }).click();
		await expect(page.getByRole("table", { name: "Retrieval quality on BEIR (nDCG@10)" })).toBeVisible();
		await noSideScroll(page, "retrieval");
		await expectNoAxeViolations(page, "benchmarks retrieval");
		await fullShot(page, screenshotPath(info, "benchmarks-retrieval"));
	});

	test("Model & keys: the budget callout, the answer picker and the language numbers", async ({ page }, info) => {
		test.skip(roleOf(info) === "member", "members cannot change models");
		await page.goto(`/kbs/${PATIENT}?tab=model`);
		const callout = page.getByRole("region", { name: "Budget option" });
		await expect(callout.getByRole("heading", { name: /Save \d+%: switch to Gemma 4 26B/ })).toBeVisible();
		await expect(page.getByRole("list", { name: "Benchmarked answer models" })).toBeVisible();
		await page.getByLabel("Language switch").selectOption("custom");
		await page.getByLabel("Hindi", { exact: true }).selectOption("google/gemma-4-31b-it");
		await expect(page.getByText(/100% right register/)).toBeVisible();
		await noSideScroll(page, "model tab");
		await expectNoAxeViolations(page, "model tab benchmarks");
		await fullShot(page, screenshotPath(info, "benchmarks-model-tab"));
	});
});
