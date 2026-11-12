import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { setupOf } from "./fixtures/mock-setup";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * C8: the guided assistant setup on the mock engine (e2e/fixtures/mock-setup.ts) and the mock
 * scraper, which "crawls" the Vidya Classes fixture site (e2e/fixtures/site) whatever host was
 * typed. Across the README matrix. A member and the super admin start at the setup URL (a member
 * cannot create a knowledge base; the super admin would need a customer first).
 */
const PATIENT = "7b221003-0000-4000-8000-000000000001";
let world: World;

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth), name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

/** Answer whatever question is on screen the simplest valid way, then press Next or Finish. */
async function answerAll(page: Page) {
	const counter = page.getByText(/^Question \d+ of \d+$/);
	for (let i = 0; i < 14; i++) {
		const next = page.getByRole("button", { name: /^(Next|Finish)$/ });
		await expect(next).toBeVisible();
		const before = (await counter.textContent()) ?? "";
		const name = page.getByLabel(/assistant be called/i);
		if (await name.isVisible()) await name.fill("Asha");
		else if ((await page.getByRole("radio").count()) > 0) await page.getByRole("radio").first().check();
		else if ((await page.getByRole("checkbox").count()) > 0) await page.getByRole("checkbox").first().check();
		const finishing = /Finish/.test((await next.textContent()) ?? "");
		await next.click();
		if (finishing) return;
		await expect(counter).not.toHaveText(before, { timeout: 10_000 });
	}
}

test.describe("guided assistant setup", () => {
	test.beforeEach(async ({ context, page }, info) => {
		world = createWorld();
		await installMockEngine(context, world);
		await page.emulateMedia({ reducedMotion: "reduce" });
		await logIn(page, roleOf(info));
	});

	test("website, proposal, questions, review, checks, publish", async ({ page }, info) => {
		test.setTimeout(180_000);
		const role = roleOf(info);
		await page.goto(`/kbs/${PATIENT}/setup`);
		await expect(page.getByRole("heading", { level: 1, name: "Set up your assistant" })).toBeVisible();
		await expect(page.getByText("Setup replaces your unpublished draft")).toBeVisible();
		await checkPage(page, "setup-start");

		await page.getByRole("radio", { name: "I have a website" }).check();
		await page.getByLabel("Website address").fill("https://vidyaclasses.in/");
		await page.getByRole("button", { name: "Continue" }).click();
		await expect(page.getByRole("heading", { name: "Reading your website" })).toBeVisible();
		await expect(page.getByRole("heading", { name: "Here's what we found" })).toBeVisible({ timeout: 30_000 });
		await expect(page.getByLabel("Business name")).toHaveValue("Vidya Classes");
		await expect(page.getByText("From your website").first()).toBeVisible();
		await checkPage(page, "setup-proposal");
		await page.getByRole("button", { name: "Looks right" }).click();

		await expect(page.getByText(/^Question 1 of \d+$/)).toBeVisible();
		await expect(page.getByRole("heading", { name: "Who usually asks your business questions?" })).toBeVisible();
		await checkPage(page, "setup-question");
		await answerAll(page);

		const review = page.getByRole("table", { name: "What we set up" });
		await expect(review).toBeVisible({ timeout: 15_000 });
		await expect(review.getByRole("row", { name: /Tone/ })).toContainText("From your answer");
		await checkPage(page, "setup-review");
		await page.getByRole("button", { name: "Run checks" }).click();
		await expect(page.getByText(/\d+ of \d+ checks passed/)).toBeVisible({ timeout: 20_000 });
		await expect(page.getByText("Do you teach IIT coaching?").first()).toBeVisible();
		await checkPage(page, "setup-checks");
		await page.getByRole("button", { name: "Continue to publish" }).click();

		const publish = page.getByRole("button", { name: "Publish", exact: true });
		if (role === "member") {
			await expect(publish).toHaveAttribute("aria-disabled", "true");
			await checkPage(page, "setup-publish-member");
		} else {
			await checkPage(page, "setup-publish");
			await publish.click();
			await expect(page.getByRole("heading", { name: "Your assistant is live" })).toBeVisible();
			await expect(page.getByRole("link", { name: "Try it in the playground" })).toBeVisible();
			await checkPage(page, "setup-live");
			const published = world.assistant.byKb.get(PATIENT)?.published;
			expect(published?.version).toBe(4);
			expect(published?.config.identity).toMatchObject({ assistant_name: "Asha", business_name: "Vidya Classes" });
		}
		const calls = setupOf(world).answerCalls;
		expect(calls.at(0)?.partial).toBe(true);
		expect(calls.at(-1)?.partial).toBe(false);
	});

	test("no AI model: the website step offers a business type instead", async ({ page }) => {
		setupOf(world).llm = false;
		await page.goto(`/kbs/${PATIENT}/setup`);
		await page.getByRole("radio", { name: "I have a website" }).check();
		await page.getByLabel("Website address").fill("https://vidyaclasses.in/");
		await page.getByRole("button", { name: "Continue" }).click();
		await expect(page.getByText(/No AI model is set up/)).toBeVisible({ timeout: 30_000 });
		await checkPage(page, "setup-no-model");
		await page.getByRole("button", { name: "Choose a business type instead" }).click();
		await expect(page.getByRole("radio", { name: "Clinic or lab" })).toBeVisible();
	});

	test("owners reach the setup from the new knowledge base dialog", async ({ page }, info) => {
		test.skip(roleOf(info) !== "owner", "only an owner creates a knowledge base");
		await page.goto("/kbs");
		await page.getByRole("button", { name: "New knowledge base" }).first().click();
		await page.getByRole("dialog").getByLabel("Name").fill("Shree Labs help");
		await page.getByRole("dialog").getByRole("button", { name: "Create knowledge base" }).click();
		await page.getByRole("button", { name: "Set up the assistant now" }).click();
		await expect(page.getByRole("heading", { level: 1, name: "Set up your assistant" })).toBeVisible();
	});
});
