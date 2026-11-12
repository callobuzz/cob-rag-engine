import { expect, test } from "@playwright/test";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, roleOf } from "./utils/session";

/**
 * BIL1 provider balances on the Costs page, against the mock engine (e2e/fixtures/mock-providers.ts):
 * the super admin edits OpenAI, saves an admin key and the card says "Key saved" (the key never
 * comes back), and refreshing twice in a minute is refused politely. Super admin only.
 */
test.beforeEach(async ({ context }, info) => {
	test.skip(roleOf(info) !== "superadmin", "Provider balances are the super admin's page");
	await installMockEngine(context, createWorld());
});

test("edit OpenAI, save a key, refresh twice", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await page.goto("/admin/costs");
	const panel = page.getByTestId("provider-balances");
	await expect(panel).toBeVisible();
	const openai = panel.locator('[data-provider="openai"]');
	await expect(openai).toContainText("Not set up");

	await openai.getByRole("button", { name: "Edit OpenAI" }).click();
	const dialog = page.getByRole("dialog");
	const key = dialog.getByLabel("Admin key");
	await expect(key).toHaveValue("");
	await key.fill("sk-admin-E2E-SECRET");
	await dialog.getByRole("button", { name: "Save" }).click();
	await expect(dialog).toBeHidden();
	await expect(openai).toContainText("Key saved");
	await expect(page.locator("body")).not.toContainText("sk-admin-E2E-SECRET");

	const refresh = panel.locator('[data-provider="openrouter"]').getByRole("button", { name: "Refresh OpenRouter" });
	await refresh.click();
	await expect(page.getByText("OpenRouter refreshed")).toBeVisible();
	await refresh.click();
	await expect(page.getByText(/Try again in a minute/)).toBeVisible();

	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await expectNoAxeViolations(page, "provider-balances");
});
