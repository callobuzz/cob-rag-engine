import { expect, test } from "@playwright/test";
import { billingStateFor } from "./fixtures/mock-billing";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * P6 choosing and changing a plan, on the mock engine (e2e/fixtures/mock-plan-choice.ts): an owner
 * on Free sees what moving up costs today, moves up to Standard (₹515.61 for the 16 days left),
 * schedules a move back down for the renewal and calls it off; a move up the balance cannot cover
 * goes to the pay page for the amount; a member never sees the card; a visitor's plan from the
 * pricing page shows on the sign-up form.
 */
let world: World;

test.beforeEach(async ({ context }) => {
	world = createWorld();
	billingStateFor(world).billed = true;
	billingStateFor(world).balance = world.planChoice.available;
	await installMockEngine(context, world);
});

test("an owner moves up at once, then schedules a move down and calls it off", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's page");
	await logIn(page, "owner");
	await page.goto("/plan");
	await expect(page.getByRole("heading", { level: 1, name: "Plan & billing" })).toBeVisible();
	const card = page.getByTestId("change-plan-card");
	await expect(card).toBeVisible();
	const standard = card.locator('[data-plan="standard"]');
	await expect(standard.getByTestId("plan-choice-price")).toHaveText("₹999.00 a month + GST · ₹1,178.82 incl. GST");
	await expect(standard.getByTestId("plan-choice-note")).toHaveText("₹515.61 today for the rest of this period");
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await expectNoAxeViolations(page, "change-plan-card");
	await fullShot(page, screenshotPath(info, "plan-choice-owner"));

	await standard.getByRole("button", { name: "Upgrade to Standard" }).click();
	const confirm = page.getByRole("alertdialog", { name: "Upgrade to Standard?" });
	await expect(confirm.getByTestId("plan-confirm-body")).toContainText("₹515.61 comes from your balance now");
	expect(world.planChoice.sent).toHaveLength(0);
	await confirm.getByRole("button", { name: "Upgrade now" }).click();
	await expect(page.getByText("You're on Standard now")).toBeVisible();
	await expect(standard.getByText("Your plan")).toBeVisible();
	expect(world.planChoice.available).toBe(300_000 - 51_561);

	const free = card.locator('[data-plan="free"]');
	await free.getByRole("button", { name: "Move to Free" }).click();
	await page
		.getByRole("alertdialog", { name: "Move to Free?" })
		.getByRole("button", { name: "Move at renewal" })
		.click();
	await expect(card.getByTestId("plan-pending")).toContainText("Moving to Free on 1 Nov 2026.");
	await card.getByRole("button", { name: "Keep my current plan" }).click();
	await page
		.getByRole("alertdialog", { name: "Keep your current plan?" })
		.getByRole("button", { name: "Keep my plan" })
		.click();
	await expect(card.getByTestId("plan-pending")).toHaveCount(0);
	expect(world.planChoice.sent.map((s) => s.method)).toEqual(["POST", "POST", "DELETE"]);
});

test("a move up the balance cannot cover goes to the pay page for the amount", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's page");
	world.planChoice.available = 100_000;
	await logIn(page, "owner");
	await page.goto("/plan");
	const pro = page.getByTestId("change-plan-card").locator('[data-plan="pro"]');
	await pro.getByRole("button", { name: "Upgrade to Pro" }).click();
	const confirm = page.getByRole("alertdialog", { name: "Upgrade to Pro?" });
	await expect(confirm.getByTestId("plan-confirm-pay-first")).toContainText("Your balance cannot cover ₹2,580.12 yet");
	await confirm.getByRole("button", { name: "Continue to payment" }).click();
	await page.waitForURL(/\/pay\?purpose=top_up&amount=\d+/);
	expect(new URL(page.url()).searchParams.get("amount")).toBe("158100");
});

test("a member does not see the plan change card", async ({ page }, info) => {
	test.skip(roleOf(info) !== "member", "the member's page");
	await logIn(page, "member");
	await page.goto("/plan");
	await expect(page.getByTestId("plan-meters")).toBeVisible();
	await expect(page.getByTestId("change-plan-card")).toHaveCount(0);
});

test("the plan chosen on the pricing page shows on the sign-up form", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "once per viewport is enough");
	await page.goto("/signup?plan=pro");
	const chosen = page.getByTestId("signup-plan");
	await expect(chosen).toContainText("Pro");
	await expect(chosen.getByTestId("signup-plan-price")).toHaveText("₹4,999.00 a month · ₹5,898.82 incl. GST");
	await expectNoAxeViolations(page, "signup-plan");
	await fullShot(page, screenshotPath(info, "plan-choice-signup"));
});
