import { expect, test } from "@playwright/test";
import { billingStateFor } from "./fixtures/mock-billing";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, roleOf } from "./utils/session";

/**
 * BIL4: a workspace whose balance ran out sees the banner (owner: link to the plan page; member:
 * "ask your owner"), and the playground answers with the pause message. Workspace views only.
 */
test.describe("paused workspace (BIL4)", () => {
	test.beforeEach(async ({ browserName: _b }, info) => {
		test.skip(roleOf(info) === "superadmin", "The banner is a workspace view");
	});

	test("a paused balance shows the banner and the playground answers with the pause message", async ({
		page,
		context,
	}, info) => {
		const world = createWorld();
		billingStateFor(world).paused = true;
		await installMockEngine(context, world);
		await logIn(page, roleOf(info));
		const banner = page.getByTestId("billing-banner-paused");
		await expect(banner).toBeVisible();
		if (roleOf(info) === "member") {
			await expect(banner).toContainText("Ask your workspace owner to top up");
			await expect(banner.getByRole("link")).toHaveCount(0);
		} else {
			await expect(banner).toContainText("your balance has run out");
			await expect(banner.getByRole("link", { name: "Plan & billing" })).toBeVisible();
		}
		await page.goto("/playground?kb=7b221003-0000-4000-8000-000000000001");
		await page.getByRole("textbox", { name: "Question" }).fill("What are your prices?");
		await page.getByRole("button", { name: /^Ask$/ }).click();
		await expect(page.getByTestId("bot-answer").last()).toContainText("our team will be happy to help you directly");
		if (isPhone(page)) {
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
		}
		await expectNoAxeViolations(page, "billing-banner-paused");
	});

	test("an unpaused workspace shows no banner", async ({ page, context }, info) => {
		await installMockEngine(context, createWorld());
		await logIn(page, roleOf(info));
		// BIZ3: a business lands on Home, the operator on Overview
		await expect(
			page.getByRole("heading", { name: roleOf(info) === "superadmin" ? "Overview" : "Home" }),
		).toBeVisible();
		await expect(page.getByTestId("billing-banner-paused")).toHaveCount(0);
	});
});
