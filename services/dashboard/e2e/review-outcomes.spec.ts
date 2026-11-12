import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { createEvalsState, type EvalsState, installEvalsMock } from "./fixtures/mock-evals";
import { createInsightsState, installInsightsMock } from "./fixtures/mock-insights";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * C7: golden sets, test runs with a cost confirmation, comparison, the review inbox and the
 * Outcomes tab on the mock engine (e2e/fixtures/mock-evals.ts on top of mock-engine.ts), across
 * the README matrix. Indian demo data: Shree Labs, "Patient help".
 */
const PATIENT = "7b221003-0000-4000-8000-000000000001";
let state: EvalsState;

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth), name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

async function startFullRun(page: Page, name: string) {
	await page.getByRole("button", { name: "New run" }).click();
	const dlg = page.getByRole("dialog", { name: "New run" });
	await dlg.getByLabel("Golden set").selectOption({ label: "Template questions (clinic)" });
	await dlg.getByRole("radio", { name: /Full run/ }).check();
	await dlg.getByLabel("Name (optional)").fill(name);
	await dlg.getByRole("button", { name: "Estimate cost" }).click();
	await expect(dlg.getByText(/for 3 questions/)).toBeVisible();
	await expect(dlg.getByRole("button", { name: "Start run" })).toHaveAttribute("aria-disabled", "true");
	// aria-disabled keeps the button focusable and clickable: the click shows what is missing.
	await dlg.getByRole("button", { name: "Start run" }).click({ force: true });
	await expect(dlg.getByText("Tick the box to confirm the cost")).toBeVisible();
	await dlg.getByRole("checkbox", { name: /I understand this run costs about/ }).check();
	await dlg.getByRole("button", { name: "Start run" }).click();
	await expect(page.getByText("2 of 3 passed").first()).toBeVisible();
}

test.describe("golden sets, test runs, review and outcomes", () => {
	test.beforeEach(async ({ context, page }, info) => {
		const world = createWorld();
		await installMockEngine(context, world);
		await installInsightsMock(context, world, createInsightsState(world));
		state = createEvalsState();
		await installEvalsMock(context, world, state);
		await logIn(page, roleOf(info));
	});

	test("template set, quick check, full runs with the cost confirmed, compare", async ({ page }) => {
		test.setTimeout(120_000);
		await page.goto(`/quality?kb=${PATIENT}&tab=golden`);
		await page.getByRole("button", { name: "Add the template's tests" }).click();
		const sets = page.getByRole("table", { name: "Golden sets" });
		await expect(sets.getByText("Template questions (clinic)")).toBeVisible();
		await expect(sets.getByText("3 questions · 3 with expected behaviour")).toBeVisible();
		await checkPage(page, "quality-golden-sets");

		await page.getByRole("tab", { name: "Runs" }).click();
		await page.getByRole("button", { name: "New run" }).click();
		const dlg = page.getByRole("dialog", { name: "New run" });
		await dlg.getByLabel("Golden set").selectOption({ label: "Template questions (clinic)" });
		await expect(dlg.getByRole("button", { name: "Estimate cost" })).toHaveCount(0);
		await dlg.getByRole("button", { name: "Start run" }).click();
		await expect(page.getByText("2 of 3 passed").first()).toBeVisible();
		await page.getByRole("button", { name: "All runs" }).click();

		await startFullRun(page, "baseline");
		await page.getByRole("button", { name: "All runs" }).click();
		await startFullRun(page, "re-ranking on");
		await expect(page.getByText(/95 % CI/).first()).toBeVisible();
		await page.getByRole("switch", { name: "Failed only" }).click();
		await expect(page.getByText("Can you diagnose my fever?")).toBeVisible();
		await page.getByRole("button", { name: "Show details" }).first().click();
		await expect(page.getByText("Gives medical advice instead of declining.")).toBeVisible();
		await checkPage(page, "quality-run-detail");

		await page.getByRole("tab", { name: "Compare" }).click();
		await page.getByRole("checkbox", { name: /baseline/ }).check();
		await page.getByRole("checkbox", { name: /re-ranking on/ }).check();
		await expect(page.getByText("not significant")).toBeVisible();
		await expect(page.getByText("Some runs have fewer than 30 questions: differences may be noise.")).toBeVisible();
		await checkPage(page, "quality-compare");
	});

	test("the judge is changed by an owner and refused to a member", async ({ page }, info) => {
		await page.goto(`/quality?kb=${PATIENT}&tab=criteria`);
		await expect(page.getByText("mentions-home-fee")).toBeVisible();
		const save = page.getByRole("button", { name: "Save judge" });
		if (roleOf(info) === "member") {
			await expect(save).toHaveAttribute("aria-disabled", "true");
		} else {
			await page.getByLabel("Model", { exact: true }).fill("judge-x");
			await save.click();
			await expect(page.getByText("Saved").first()).toBeVisible();
			expect(state.judge.model).toBe("judge-x");
		}
		await checkPage(page, "quality-criteria");
	});

	test("review inbox adds an exact answer to the draft", async ({ page }) => {
		await page.goto(`/review?kb=${PATIENT}`);
		await expect(page.getByRole("heading", { level: 1, name: "Review" })).toBeVisible();
		await checkPage(page, "review-inbox");
		const card = page.getByRole("article", { name: "Do you do MRI scans?" });
		await expect(card.getByRole("button", { name: "Add an exact answer" })).toBeEnabled();
		await card.getByRole("button", { name: "Add an exact answer" }).click();
		const dlg = page.getByRole("dialog", { name: "Add an exact answer" });
		await dlg.getByLabel("Topic").fill("MRI scans");
		await dlg.getByLabel("Answer").fill("We are a pathology lab, so we don't do MRI scans.");
		await expectNoAxeViolations(page, "review answer dialog");
		await dlg.getByRole("button", { name: "Add to draft" }).click();
		await expect(page.getByText(/Publish to make it live/)).toBeVisible();
		await expect(page.getByRole("article", { name: "Do you do MRI scans?" })).toHaveCount(0);
		expect(state.actions[0]).toMatchObject({ action: "added_answer", item_key: "a".repeat(32) });
		expect(typeof state.actions[0]?.base_revision).toBe("number");
	});

	test("outcomes tab, with a table for every chart", async ({ page }) => {
		await page.goto(`/insights?kb=${PATIENT}`);
		await expect(page.getByRole("tab", { name: "Outcomes", selected: true })).toBeVisible();
		await expect(page.getByTestId("outcome-kpis").getByText("75%")).toBeVisible();
		await expect(page.getByText(/309 of 412 conversations were resolved without a person/)).toBeVisible();
		await page.getByRole("button", { name: "Show as table" }).first().click();
		await expect(page.getByRole("rowheader", { name: "hi-Latn" })).toBeVisible();
		await checkPage(page, "insights-outcomes");
	});
});
