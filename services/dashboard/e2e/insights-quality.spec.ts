import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { createEvalsState, installEvalsMock } from "./fixtures/mock-evals";
import { createInsightsState, type InsightsState, installInsightsMock } from "./fixtures/mock-insights";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * D9 Insights and Quality on the mock engine (e2e/fixtures/mock-insights.ts on top of
 * mock-engine.ts) across the README matrix: fixture questions in "Patient help" (English, Hindi,
 * Marathi), a completed gap report, one golden question and one memory. The same journeys against
 * the running stack are pending at the bottom of the file.
 */

const PATIENT = "7b221003-0000-4000-8000-000000000001";
let world: World;
let state: InsightsState;

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		const width = await page.evaluate(() => document.documentElement.scrollWidth);
		expect(width, name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

const main = (page: Page) => page.locator("#main");

test.beforeEach(async ({ context, page }) => {
	world = createWorld();
	await installMockEngine(context, world);
	state = createInsightsState(world);
	await installInsightsMock(context, world, state);
	await installEvalsMock(context, world, createEvalsState());
	await page.emulateMedia({ reducedMotion: "reduce" });
});

test("questions → drawer → 👎 with a correction → feedback → golden set", async ({ page }, info) => {
	test.setTimeout(90_000);
	await logIn(page, roleOf(info));
	await page.goto(`/insights?kb=${PATIENT}&tab=questions`);
	await expect(page.getByRole("heading", { level: 1, name: "Insights" })).toBeVisible();
	const table = page.getByRole("table", { name: "Question history" });
	await expect(table.getByText("Do I need to fast for a lipid profile?")).toBeVisible();
	await expect(table.getByText("लिपिड प्रोफाइल के लिए उपवास ज़रूरी है?")).toBeVisible();
	await expect(table.getByText("Weak").first()).toBeVisible();
	await checkPage(page, "insights-questions");

	await table.getByRole("button", { name: "Open question: Is HbA1c included in the diabetes package?" }).click();
	const drawer = page.getByRole("dialog", { name: "Is HbA1c included in the diabetes package?" });
	await expect(drawer.getByTestId("drawer-answer")).toHaveText(
		"The documents don't say which tests the package includes.",
	);
	await expect(drawer.getByText("openai/gpt-oss-20b")).toBeVisible();
	await drawer
		.getByLabel("What should it have said? (optional)")
		.fill("Yes. HbA1c is part of the diabetes package (₹1,499), with fasting sugar and urine routine.");
	await checkPage(page, "insights-question-drawer");
	await drawer.getByRole("button", { name: "Save feedback" }).click();
	await expect(page.getByText("Feedback saved")).toBeVisible();
	await expect(drawer.getByTestId("drawer-feedback")).toContainText("Not helpful");
	await page.keyboard.press("Escape");
	await expect(drawer).toBeHidden();

	await page.getByRole("tab", { name: "Feedback" }).click();
	const fb = page.getByRole("table", { name: "Feedback" });
	await expect(fb.getByText("HbA1c is part of the diabetes package")).toBeVisible();
	await expect(page.getByTestId("feedback-stats")).toContainText("Corrections");
	await checkPage(page, "insights-feedback");
	await fb.getByRole("button", { name: "Add “Is HbA1c included in the diabetes package?” to the golden set" }).click();
	await expect(page.getByText("Added to the golden set")).toBeVisible();

	await page.goto(`/quality?kb=${PATIENT}`);
	const golden = page.getByRole("table", { name: "Golden set · Patient help" });
	await expect(golden.getByText("Is HbA1c included in the diabetes package?")).toBeVisible();
	await expect(golden.getByText("From feedback")).toBeVisible();
	await checkPage(page, "quality-golden");
});

test("golden set: import a 3-row CSV of Hindi and English pairs", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await page.goto(`/quality?kb=${PATIENT}`);
	await expect(page.getByRole("table", { name: "Golden set · Patient help" })).toBeVisible();
	await page.getByRole("button", { name: "Import CSV" }).click();
	const dlg = page.getByRole("dialog", { name: "Import golden questions" });
	const csv =
		"query,expected_answer\r\n" +
		"Is home collection free?,Free for orders above ₹999 within Pune city.\r\n" +
		'रविवार को लैब खुली है?,"हाँ, रविवार सुबह 7 से 11 बजे तक।"\r\n' +
		'"Can I get reports on WhatsApp?","Yes, on +91 98220 12345 once the report is signed."\r\n';
	await dlg
		.getByLabel("CSV file")
		.setInputFiles({ name: "golden-faq.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
	await expect(dlg.getByTestId("import-preview")).toHaveText("3 rows ready · 0 will be skipped");
	await expectNoAxeViolations(page, "import dialog");
	await dlg.getByRole("button", { name: "Import" }).click();
	await expect(page.getByText("Imported 3 golden questions")).toBeVisible();
	const golden = page.getByRole("table", { name: "Golden set · Patient help" });
	await expect(golden.getByText("रविवार को लैब खुली है?")).toBeVisible();
	await expect(golden.getByText("Can I get reports on WhatsApp?")).toBeVisible();
	await expect(golden.getByText("Imported").first()).toBeVisible();
	expect(state.golden.filter((p) => p.source === "import")).toHaveLength(3);
});

test("trends, confidence and a gap detection run", async ({ page }, info) => {
	test.setTimeout(90_000);
	await logIn(page, roleOf(info));
	await page.goto(`/insights?kb=${PATIENT}&tab=trends`);
	await expect(page.getByTestId("trend-count-chart")).toBeVisible();
	await expect(page.getByTestId("trend-count-chart").locator(".recharts-bar-rectangle").first()).toBeVisible();
	await expect(page.getByTestId("trend-confidence-chart")).toBeVisible();
	await checkPage(page, "insights-trends");
	await page.getByRole("button", { name: "Show as table" }).click();
	await expect(page.getByRole("table", { name: "Trend data" })).toBeVisible();

	await page.getByRole("tab", { name: "Confidence" }).click();
	await expect(page.getByTestId("confidence-summary")).toContainText("4 scored answers, 2 of them 0.8 or higher");
	await checkPage(page, "insights-confidence");

	await page.getByRole("tab", { name: "Knowledge gaps" }).click();
	await expect(page.getByRole("table", { name: "Knowledge gap reports" })).toBeVisible();
	if (roleOf(info) === "superadmin") {
		await expect(page.getByRole("region", { name: "Latest engine-wide report" })).toContainText("baner");
	} else {
		await expect(page.getByRole("region", { name: "Latest engine-wide report" })).toHaveCount(0);
	}
	await checkPage(page, "insights-gaps");
	await page.getByRole("button", { name: "Run gap detection" }).click();
	await expect(page.getByTestId("gap-polling")).toBeVisible();
	const clusters = page.getByTestId("gap-clusters");
	await expect(clusters.getByText("hba1c", { exact: true })).toBeVisible({ timeout: 15_000 });
	await expect(clusters.getByRole("link", { name: "Add content for this" })).toHaveAttribute(
		"href",
		`/add?kb=${PATIENT}`,
	);
	await checkPage(page, "insights-gap-report");
});

test("memories: an owner's typed bulk delete; a member is refused", async ({ page }, info) => {
	const role = roleOf(info);
	await logIn(page, role);
	await page.goto(`/insights?kb=${PATIENT}&tab=memories&user=visitor-17`);
	const table = page.getByRole("table", { name: "Memories" });
	await expect(table.getByText("Prefers home collection in Kothrud before 9 am")).toBeVisible();
	await checkPage(page, "insights-memories");
	const bulk = main(page).getByRole("button", { name: "Delete all for this filter" });
	if (role === "member") {
		await expect(bulk).toHaveAttribute("aria-disabled", "true");
		// aria-disabled (never silently disabled): the click still reaches the gate and shows the toast.
		await bulk.click({ force: true });
		await expect(page.getByText("Only owners can delete memories in bulk").first()).toBeVisible();
		await expect(page.getByRole("alertdialog")).toHaveCount(0);
		expect(state.memories).toHaveLength(1);
		return;
	}
	await bulk.click();
	const dlg = page.getByRole("alertdialog", { name: "Delete memories in bulk?" });
	await expect(dlg.getByTestId("bulk-scope")).toHaveText(
		"This deletes every memory of person “visitor-17” in Patient help.",
	);
	await expectNoAxeViolations(page, "bulk delete dialog");
	await dlg.getByLabel(/Type delete to confirm/).fill("delete");
	await dlg.getByRole("button", { name: "Delete" }).click();
	await expect(page.getByText("1 memory deleted")).toBeVisible();
	await expect(table.getByText("No memories for this filter.")).toBeVisible();
	expect(state.memories).toHaveLength(0);
});

test("evaluation runs: the super admin triggers one on past traffic; tenants see their runs and an optimisation note", async ({
	page,
}, info) => {
	const role = roleOf(info);
	await logIn(page, role);
	await page.goto(`/quality?kb=${PATIENT}&tab=runs`);
	if (role !== "superadmin") {
		await expect(page.getByRole("table", { name: "Evaluation runs" })).toHaveCount(0);
		await expect(page.getByRole("button", { name: "New run" })).toBeVisible();
		await page.getByRole("tab", { name: "Optimisation" }).click();
		await expect(page.getByTestId("tenant-note")).toContainText("Optimisation is run by Sahayak AI");
		await checkPage(page, "quality-evals-tenant");
		return;
	}
	await expect(page.getByRole("table", { name: "Evaluation runs" })).toBeVisible();
	await page.getByRole("button", { name: "Run evaluation" }).click();
	const dlg = page.getByRole("dialog", { name: "Run an evaluation" });
	await dlg.getByLabel("How many golden questions (optional)").fill("10");
	await expectNoAxeViolations(page, "run evaluation dialog");
	await dlg.getByRole("button", { name: "Run evaluation" }).click();
	await expect(page.getByText("Evaluation queued")).toBeVisible();
	await expect(main(page).getByText("Queued", { exact: true })).toBeVisible();
	await expect(page.getByTestId("run-polling")).toBeVisible();
	expect(state.evals[0]).toMatchObject({ status: "queued", corpus_id: PATIENT, sample_size: 10 });
	await checkPage(page, "quality-eval-run");
	await page.getByRole("tab", { name: "Optimisation" }).click();
	await expect(page.getByRole("table", { name: "Optimisation runs" })).toBeVisible();
	await checkPage(page, "quality-optimize");
});

// The same journeys against the running stack (docker compose + a seeded "Patient help" with
// questions, feedback enabled, an embedder for gap detection and a Celery worker for runs):
// pending until the e2e stack fixtures cover analytics data.
test.describe("insights and quality against the running stack", () => {
	test.fixme(true, "real-stack e2e pending: needs seeded question history and a worker");
	test("questions, feedback, golden set, gap run and evaluation", async () => {});
});
