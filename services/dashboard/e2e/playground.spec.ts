import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * D5 playground and conversations on the mock engine (e2e/fixtures/mock-query.ts), across the
 * README matrix: a streamed answer with citations, sources with score bars and the pipeline
 * strip, feedback, Try settings, Compare, conversations with a streamed reply, a Hindi question,
 * members using the playground, and A6's question cap. The same journeys against the real engine
 * (with an LLM) are pending at the bottom of the file.
 */

const PATIENT = "7b221003-0000-4000-8000-000000000001";
const QUESTION = "Do I need to fast for a lipid profile test?";

let world: World;

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		const widths = await page.evaluate(() => ({
			doc: document.documentElement.scrollWidth,
			main: document.getElementById("main")?.getBoundingClientRect().width ?? 0,
		}));
		expect(widths.doc, name).toBeLessThanOrEqual(390);
		expect(widths.main, name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

async function ask(page: Page, question: string) {
	const input = page.getByRole("textbox", { name: "Question" });
	await input.fill(question);
	await page.getByRole("button", { name: /^Ask$|^Compare \(uses 2 questions\)$/ }).click();
}

const lastAnswer = (page: Page) => page.getByTestId("bot-answer").last();

test.describe("playground", () => {
	test.beforeEach(async ({ context, page }, info) => {
		world = createWorld(info.title.includes("question cap") ? { questionLimit: 1 } : {});
		await installMockEngine(context, world);
		await page.emulateMedia({ reducedMotion: "reduce" });
	});

	test("answer with sources, pipeline, feedback, try settings, compare and conversations", async ({ page }, info) => {
		test.setTimeout(180_000);
		const role = roleOf(info);
		await logIn(page, role);

		await page.goto(`/playground?kb=${PATIENT}`);
		await expect(page.getByRole("heading", { level: 1, name: "Playground" })).toBeVisible();
		await expect(page.getByText("Ask Patient help anything and see exactly how the answer was found")).toBeVisible();
		await checkPage(page, "playground-empty");

		await ask(page, QUESTION);
		const bot = lastAnswer(page);
		await expect(bot).toHaveAttribute("data-status", "done");
		await expect(bot.getByTestId("answer-text")).toContainText("Fast for 10–12 hours");
		await expect(bot.getByRole("list", { name: "Sources cited in this answer" }).getByRole("button")).toHaveCount(3);
		const meta = bot.getByTestId("answer-meta");
		await expect(meta).toContainText("openai/gpt-oss-20b");
		await expect(meta).toContainText("1,280 tokens · ≈ ₹");
		const sources = page.getByRole("list", { name: "Passages for the selected answer" }).getByTestId("source");
		await expect(sources).toHaveCount(3);
		await expect(sources.first()).toContainText("meaning 0.82");
		await expect(page.getByTestId("pipeline-cell").filter({ hasText: "Write" })).toHaveAttribute("data-state", "slow");
		await expect(page.getByTestId("pipeline-cell").filter({ hasText: "Re-rank" })).toHaveAttribute("data-state", "off");
		await expect(page.getByTestId("session-totals")).toContainText("This session: 1 question");

		await bot.getByRole("button", { name: /\[3\] home-collection\.pdf/ }).click();
		await expect(sources.nth(2)).toBeFocused();

		await bot.getByRole("button", { name: "Good answer" }).click();
		await expect(bot.getByText("Thanks, your feedback was sent.")).toBeVisible();
		expect(world.query.feedback).toEqual([expect.objectContaining({ rating: 5 })]);
		await checkPage(page, "playground-answer");

		await page.getByRole("button", { name: "Try settings" }).click();
		const panel = page.getByRole("region", { name: "Settings for this question only" });
		await expect(panel.getByText("Nothing is saved")).toBeVisible();
		await panel.getByRole("radio", { name: "Keywords" }).click();
		await panel.getByLabel("Passages given").fill("2");
		await checkPage(page, "playground-try");

		await page.getByRole("button", { name: "Compare" }).click();
		await expect(page.getByText(/it uses 2 questions of the plan/)).toBeVisible();
		await page.getByRole("textbox", { name: "Question" }).fill(QUESTION);
		await page.getByRole("button", { name: "Compare (uses 2 questions)" }).click();
		const why = page.getByRole("region", { name: "Why they differ" });
		await expect(why).toBeVisible();
		await expect(page.getByRole("heading", { level: 3, name: /Saved settings/ })).toBeVisible();
		await expect(page.getByRole("heading", { level: 3, name: /Try settings/ })).toBeVisible();
		await expect(why).toContainText("0.97 · +0.03");
		await expect(page.getByTestId("session-totals")).toContainText("This session: 3 questions");
		await checkPage(page, "playground-compare");

		await page.goto(`/conversations?kb=${PATIENT}`);
		await expect(page.getByRole("heading", { level: 1, name: "Conversations" })).toBeVisible();
		await expect(page.getByText("No conversations yet")).toBeVisible();
		await checkPage(page, "conversations-empty");
		await page.getByRole("button", { name: "New conversation" }).click();
		await expect(page.getByText("No messages yet")).toBeVisible();
		await page.getByRole("textbox", { name: "Message" }).fill("Sunday ko Baner mein home collection milega?");
		await page.getByRole("button", { name: "Send" }).click();
		const thread = page.getByRole("log", { name: "Messages" });
		await expect(thread.getByText(/₹999 से ऊपर के ऑर्डर पर/)).toBeVisible();
		await expect(page.getByTestId("reply")).toHaveCount(0);
		await expect(
			page.getByRole("region", { name: "Conversations" }).getByText("2 messages", { exact: false }),
		).toBeVisible();
		await checkPage(page, "conversations-thread");
	});

	test("a Hindi question gets a Hindi answer", async ({ page }, info) => {
		test.skip(roleOf(info) !== "owner", "owner journey");
		await logIn(page, "owner");
		await page.goto(`/playground?kb=${PATIENT}`);
		await ask(page, "क्या लिपिड प्रोफ़ाइल से पहले उपवास ज़रूरी है?");
		const bot = lastAnswer(page);
		await expect(bot).toHaveAttribute("data-status", "done");
		await expect(bot.getByTestId("answer-text")).toContainText("10–12 घंटे का उपवास");
		await expectNoAxeViolations(page, "hindi answer");
	});

	test("question cap: the second question shows the limit banner", async ({ page }, info) => {
		test.skip(roleOf(info) !== "owner", "tenant owner with monthly_questions 1");
		await logIn(page, "owner");
		await page.goto(`/playground?kb=${PATIENT}`);
		await ask(page, QUESTION);
		await expect(lastAnswer(page)).toHaveAttribute("data-status", "done");
		await ask(page, "Home collection in Kothrud on Sunday?");
		const banner = page.getByTestId("cap-banner");
		await expect(banner).toContainText("Monthly question limit reached (1 of 1). It resets on");
		await expect(banner).toContainText("Contact support@sahayak.ai.");
		await expect(banner.getByRole("link", { name: "See plan" })).toBeVisible();
		await expect(page.getByRole("textbox", { name: "Question" })).toBeDisabled();
		await expect(page.getByText(/Questions are paused/)).toBeVisible();
		await expectNoAxeViolations(page, "question cap");
		await fullShot(page, screenshotPath(info, "playground-cap"));
	});
});

/**
 * The same journeys against the real engine need an LLM (the stack's `.env` LLM, or a corpus
 * `llm_config` for a local Ollama) and A6 (`question_cap_reached`, `monthly_questions`) merged and
 * the stack rebuilt.
 */
test.describe("playground on the real engine", () => {
	test.fixme("streamed answer, feedback, Hindi, compare, capped plan and member use against the running stack (needs an LLM + A6)", async () => {});
});
