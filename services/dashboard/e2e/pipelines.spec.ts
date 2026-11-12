import { expect, test } from "@playwright/test";
import { createAdminState, installAdminMock } from "./fixtures/mock-admin";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { pipelinesStateFor } from "./fixtures/mock-pipelines";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, roleOf } from "./utils/session";

/**
 * PIP4 on the mock engine (e2e/fixtures/mock-pipelines.ts): the deployer's pipeline profiles page,
 * and what a business owner sees when answers are slower or paused.
 */
test.describe("pipeline profiles (deployer)", () => {
	test.beforeEach(async ({ browserName: _b }, info) => {
		test.skip(roleOf(info) !== "superadmin", "Only the platform operator manages pipeline profiles");
	});

	test("clone Premium as Festive rush, make it high priority, make it the default", async ({ page, context }, info) => {
		await installMockEngine(context, createWorld());
		await logIn(page, roleOf(info));
		await page.goto("/admin/pipelines");
		await expect(page.getByRole("heading", { name: "Pipeline profiles" })).toBeVisible();

		await page.getByRole("button", { name: "Clone Premium" }).click();
		const clone = page.getByRole("dialog");
		await clone.getByLabel("Name of the copy").fill("Festive rush");
		await clone.getByRole("button", { name: "Clone", exact: true }).click();
		const card = page.getByRole("listitem", { name: "Festive rush" });
		await expect(card).toBeVisible();

		await card.getByRole("button", { name: "Edit Festive rush" }).click();
		const dialog = page.getByRole("dialog");
		await dialog.getByLabel("Priority").selectOption("high");
		await dialog.getByRole("button", { name: "Save changes" }).click();
		await expect(dialog).toBeHidden();
		await expect(card).toContainText("High");

		await card.getByRole("button", { name: "Make default Festive rush" }).click();
		await expect(card.getByText("Default", { exact: true })).toBeVisible();
		await expect(page.getByRole("listitem", { name: "Standard" }).getByText("Default", { exact: true })).toHaveCount(0);
		if (isPhone(page)) {
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
		}
		await expectNoAxeViolations(page, "pipelines");
	});

	test("the Engine page has the safety rails and what the engine is answering right now", async ({
		page,
		context,
	}, info) => {
		const world = createWorld();
		await installMockEngine(context, world);
		await installAdminMock(context, world, createAdminState(world));
		await logIn(page, roleOf(info));
		await page.goto("/admin/engine");
		const rails = page.getByRole("region", { name: "Safety rails" });
		await expect(rails.getByLabel("AI spend a day")).toBeVisible();
		await expect(page.getByRole("region", { name: "Answers right now" }).getByText("3 of 8 answering")).toBeVisible();
		await expectNoAxeViolations(page, "engine-rails");
	});

	test("a customer's Answer speed & limits tab: an own limit and a different profile", async ({
		page,
		context,
	}, info) => {
		const world = createWorld();
		await installMockEngine(context, world);
		await logIn(page, roleOf(info));
		const tenant = world.tenants.find((t) => t.name === "Rangoli Sarees");
		await page.goto(`/admin/customers/${tenant?.id}?tab=limits`);
		await expect(page.getByRole("tab", { name: "Answer speed & limits", selected: true })).toBeVisible();
		await expect(page.getByTestId("fair-use-today")).toContainText("Today: 250 answered, 38 slower, 3 paused");
		const limits = page.getByRole("region", { name: "Limits" });
		await expect(limits.getByLabel("Questions an hour")).toHaveValue("follow");
		await limits.getByLabel("Questions an hour").selectOption("custom");
		await limits.getByLabel("Questions an hour, own limit").fill("60");
		await limits.getByRole("button", { name: "Save limits" }).click();
		await expect(limits.getByText("Own", { exact: true })).toBeVisible();

		const speed = page.getByRole("region", { name: "Answer speed" });
		await speed.getByLabel("Pipeline profile").selectOption({ label: "Premium" });
		await speed.getByRole("button", { name: "Save answer speed" }).click();
		await expect(speed.getByText(/Answers now use Premium/)).toBeVisible();
		if (isPhone(page)) {
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
		}
		await expectNoAxeViolations(page, "customer-limits");
	});
});

test.describe("answer speed and fair use (business owner)", () => {
	test.beforeEach(async ({ browserName: _b }, info) => {
		test.skip(roleOf(info) === "superadmin", "A workspace view");
	});

	test("a throttled workspace sees the banner in the product's name and its limits on Plan & billing", async ({
		page,
		context,
	}, info) => {
		const world = createWorld();
		pipelinesStateFor(world).owner = {
			answer_speed: "Economy",
			state: "throttled",
			billing_block: "pause_handoff",
			metrics: [
				{
					metric: "questions_per_day",
					label: "questions today",
					used: 340,
					limit: 300,
					pct: 113,
					level: "soft",
					resets_at: "2026-10-06T00:00:00Z",
				},
				{
					metric: "ai_spend_per_day",
					label: "AI usage today",
					used: null,
					limit: null,
					pct: 82,
					level: "near",
					resets_at: null,
				},
			],
		};
		await installMockEngine(context, world);
		await logIn(page, roleOf(info));
		const banner = page.getByTestId("fair-use-banner-throttled");
		await expect(banner).toBeVisible();
		await expect(banner).toContainText("You've passed today's question limit, so answers are a little slower until");
		await expect(page.getByRole("status", { name: "Notice from Sahayak AI" })).toBeVisible();
		if (roleOf(info) === "member") await expect(banner.getByRole("link")).toHaveCount(0);
		else await expect(banner.getByRole("link", { name: "See Plan & billing" })).toBeVisible();

		await page.goto("/plan");
		const card = page.getByRole("region", { name: "Fair use" });
		await expect(card.getByTestId("owner-answer-speed")).toContainText("Answer speed: Economy");
		await expect(card.getByRole("meter", { name: "Questions today" })).toBeVisible();
		await expect(card.getByRole("meter", { name: "AI usage today" })).toHaveAttribute("aria-valuenow", "82");
		expect(await card.textContent()).not.toMatch(/[$₹]/);
		if (isPhone(page)) {
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
		}
		await expectNoAxeViolations(page, "plan-fair-use");
	});
});
