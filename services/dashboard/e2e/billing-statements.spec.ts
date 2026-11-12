import { expect, test } from "@playwright/test";
import { billingStateFor } from "./fixtures/mock-billing";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, roleOf } from "./utils/session";

/**
 * BIL6, against the mock engine (e2e/fixtures/mock-billing.ts): the owner reads Plan & billing,
 * opens a statement and saves its CSV, asks for a top-up and is told to wait an hour for the next;
 * the super admin reads a customer's statements and the P&L.
 */
test.describe("Plan & billing (owner)", () => {
	test.beforeEach(async ({ browserName: _b }, info) => {
		test.skip(roleOf(info) !== "owner", "Owners ask for top-ups and open their statements");
	});

	test("balance, usage and statements; View opens the page and CSV saves the file", async ({ page, context }, info) => {
		const world = createWorld();
		billingStateFor(world).billed = true;
		await installMockEngine(context, world);
		await logIn(page, roleOf(info));
		await page.goto("/plan");
		await expect(page.getByRole("heading", { level: 1, name: "Plan & billing" })).toBeVisible();
		const card = page.getByTestId("balance-card");
		await expect(card.getByTestId("balance-amount")).toHaveText("₹500.00");
		await expect(card.getByTestId("balance-hint")).toContainText("About 250 answers left at ₹2.00 each");
		await expect(page.getByTestId("usage-this-period")).toContainText("₹84.00");
		const rows = page.getByTestId("statement-row");
		await expect(rows).toHaveCount(3);
		await expect(rows.first()).toContainText("October 2026");
		await expect(rows.first()).toContainText("So far");

		const popup = page.waitForEvent("popup");
		await rows
			.nth(1)
			.getByRole("button", { name: /^View the statement for September 2026/ })
			.click();
		const statement = await popup;
		await expect(statement.getByRole("heading", { name: "Statement for 2026-09" })).toBeVisible();
		await statement.close();

		const download = page.waitForEvent("download");
		await rows
			.first()
			.getByRole("button", { name: /^Download the statement for October 2026 as CSV/ })
			.click();
		const file = await download;
		expect(file.suggestedFilename()).toBe("statement-shree-labs-2026-10.csv");

		if (isPhone(page)) {
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
		}
		await expectNoAxeViolations(page, "balance-card");
	});

	test("request a top-up: a toast, then 'try again in 1 hour'", async ({ page, context }, info) => {
		const world = createWorld();
		billingStateFor(world).billed = true;
		await installMockEngine(context, world);
		await logIn(page, roleOf(info));
		await page.goto("/plan");
		await page.getByRole("button", { name: "Request top-up" }).click();
		const dialog = page.getByRole("dialog", { name: "Request a top-up" });
		await dialog.getByLabel(/^Amount you would like to add/).fill("5000");
		await dialog.getByLabel(/^Note/).fill("Paid by UPI");
		await dialog.getByRole("button", { name: "Send request" }).click();
		await expect(page.getByText("Top-up request sent")).toBeVisible();
		await expect(dialog).toBeHidden();

		await page.getByRole("button", { name: "Request top-up" }).click();
		const again = page.getByRole("dialog", { name: "Request a top-up" });
		await again.getByRole("button", { name: "Send request" }).click();
		await expect(again).toContainText("Try again in 1 hour");
	});
});

test.describe("statements and P&L (super admin)", () => {
	test.beforeEach(async ({ browserName: _b }, info) => {
		test.skip(roleOf(info) !== "superadmin", "Customers' statements and the P&L are the super admin's");
	});

	test("the P&L shows margin per customer and switches currency", async ({ page, context }, info) => {
		await installMockEngine(context, createWorld());
		await logIn(page, roleOf(info));
		await page.goto("/admin/billing?tab=pnl");
		const table = page.getByTestId("pnl-table");
		await expect(table).toContainText("Shree Labs");
		await expect(table).toContainText("₹440.00");
		await expect(table).toContainText("Total");
		await expect(table.getByTestId("estimate-badge").first()).toBeVisible();
		await page.getByLabel("Show in").selectOption("USD");
		await expect(table).toContainText("$5.27");
		if (isPhone(page)) {
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
		}
		await expectNoAxeViolations(page, "pnl");
	});

	test("a customer's Billing tab lists their statements", async ({ page, context }, info) => {
		await installMockEngine(context, createWorld());
		await logIn(page, roleOf(info));
		await page.goto("/admin/billing");
		await page.getByRole("link", { name: "Shree Labs" }).click();
		await expect(page.getByRole("tab", { name: "Billing", selected: true })).toBeVisible();
		const panel = page.getByTestId("customer-statements");
		await expect(panel.getByTestId("statement-row")).toHaveCount(3);
		const download = page.waitForEvent("download");
		await panel.getByRole("button", { name: /^Download the statement for September 2026 as CSV/ }).click();
		expect((await download).suggestedFilename()).toMatch(/^statement-shree-labs-2026-09\.csv$/);
		await expectNoAxeViolations(page, "customer-statements");
	});
});
