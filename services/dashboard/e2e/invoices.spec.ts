import { expect, test } from "@playwright/test";
import { billingStateFor } from "./fixtures/mock-billing";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { seedInvoices } from "./fixtures/mock-invoices";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * P2 tax invoices, on the mock engine (e2e/fixtures/mock-invoices.ts): an owner sees their tax
 * invoices and a credit note on Plan & billing and downloads one PDF; the provider reads every
 * document with its totals, downloads the month's CSV for the accountant and resends an invoice.
 * The real PDF is checked in the engine's tests; here the file is a stand-in.
 */
let world: World;

test.beforeEach(async ({ context }) => {
	world = createWorld();
	billingStateFor(world).billed = true;
	const shree = world.tenants.find((t) => t.name === "Shree Labs");
	seedInvoices(world.invoices, shree?.id ?? "t");
	await installMockEngine(context, world);
});

test("an owner sees their tax invoices and downloads one", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's page");
	await logIn(page, "owner");
	await page.goto("/plan");
	await expect(page.getByRole("heading", { level: 1, name: "Plan & billing" })).toBeVisible();
	const card = page.getByTestId("invoices-card");
	await expect(card).toBeVisible();
	const rows = card.getByTestId("invoice-row");
	await expect(rows).toHaveCount(3);
	await expect(rows.first()).toContainText("CN/26-27/00001");
	await expect(rows.first()).toContainText("Against INV/26-27/00002");
	await expect(rows.first()).toContainText("−₹590.00");
	await expect(rows.last()).toContainText("INV/26-27/00001");
	await expect(rows.last()).toContainText("₹1,180.00");
	await expect(card.getByTestId("invoices-sent-to")).toContainText("owners of this workspace");
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	const download = page.waitForEvent("download");
	await card.getByRole("button", { name: "Download INV/26-27/00001" }).click();
	expect((await download).suggestedFilename()).toBe("INV-26-27-00001.pdf");
	expect(world.invoices.downloaded).toEqual(["doc-1"]);
	await expectNoAxeViolations(page, "invoices-card");
	await fullShot(page, screenshotPath(info, "invoices-owner"));
});

test("a member of the workspace never sees the tax invoices", async ({ page }, info) => {
	test.skip(roleOf(info) !== "member", "the member's page");
	await logIn(page, "member");
	await page.goto("/plan");
	await expect(page.getByRole("heading", { level: 1, name: "Plan & billing" })).toBeVisible();
	await expect(page.getByTestId("invoices-card")).toHaveCount(0);
});

test("the provider reads every document, downloads the month's CSV and resends an invoice", async ({ page }, info) => {
	test.skip(roleOf(info) !== "superadmin", "the provider's page");
	await logIn(page, "superadmin");
	await page.goto("/admin/billing?tab=invoices");
	const table = page.getByTestId("invoices-table");
	await expect(table.getByText("INV/26-27/00002")).toBeVisible();
	await expect(table.getByTestId("invoices-totals")).toContainText("₹1,000.00"); // taxable: 1,000 + 500 - 500
	await expect(table.getByTestId("invoices-totals")).toContainText("₹1,180.00"); // total: 1,180 + 590 - 590
	await expect(page.getByTestId("irn-note")).toContainText("only above ₹5 crore turnover");

	await table.getByLabel("Type").selectOption("credit_note");
	await expect(table.getByText("INV/26-27/00002")).toHaveCount(0);
	await expect(table.getByText("CN/26-27/00001")).toBeVisible();
	await table.getByLabel("Type").selectOption("");

	await table.getByRole("button", { name: "Resend INV/26-27/00001" }).click();
	await expect(page.getByText("Sent again")).toBeVisible();
	expect(world.invoices.resent).toEqual(["doc-1"]);

	const csv = page.getByTestId("invoices-csv");
	await csv.getByLabel("Month").fill("2026-10");
	const download = page.waitForEvent("download");
	await csv.getByRole("button", { name: "Download CSV for your accountant" }).click();
	expect((await download).suggestedFilename()).toBe("tax-documents-2026-10-01-2026-10-31.csv");
	expect(world.invoices.exports).toEqual(["2026-10-01..2026-10-31"]);
	await expectNoAxeViolations(page, "invoices-tab");
	await fullShot(page, screenshotPath(info, "invoices-admin"));
});
