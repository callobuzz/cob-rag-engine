import { expect, test } from "@playwright/test";
import { billingStateFor } from "./fixtures/mock-billing";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, roleOf } from "./utils/session";

/**
 * BIL2 billing, against the mock engine (e2e/fixtures/mock-billing.ts): the super admin prices a
 * plan, opens a customer on it, gives that customer their own price and sees the "Custom" badge.
 * Super admin only.
 */
test.beforeEach(async ({ browserName: _b }, info) => {
	test.skip(roleOf(info) !== "superadmin", "Billing is the super admin's page");
});

test("price a plan, then give one customer their own price", async ({ page, context }, info) => {
	const world = createWorld();
	await installMockEngine(context, world);
	await logIn(page, roleOf(info));

	await page.goto("/admin/billing?tab=pricing");
	const card = page.getByRole("article", { name: /^Standard/ });
	await expect(card).toContainText("No pricing yet");
	await card.getByRole("button", { name: "Edit pricing" }).click();
	const dialog = page.getByRole("dialog", { name: "Pricing for Standard" });
	await dialog.getByLabel("How questions are charged").selectOption("per_question");
	await dialog.getByLabel("Price per question").fill("2");
	await expect(dialog.getByTestId("pricing-preview")).toContainText("₹200.00");
	await dialog.getByRole("button", { name: "Save pricing" }).click();
	await expect(dialog).toBeHidden();
	await expect(card.getByTestId("pricing-summary")).toContainText("₹2.00 per question");
	expect(billingStateFor(world).sent).toHaveLength(1);

	await page.goto("/admin/billing");
	await expect(page.getByRole("link", { name: "Shree Labs" })).toBeVisible();
	await page.getByRole("link", { name: "Shree Labs" }).click();
	await expect(page.getByRole("tab", { name: "Billing", selected: true })).toBeVisible();
	const row = page.getByTestId("terms-row-per_question_minor");
	await expect(row).toContainText("₹2.00");
	await expect(row.getByTestId("custom-badge")).toHaveCount(0);
	await row.getByRole("switch", { name: "Custom Price per question" }).click();
	await row.getByRole("textbox", { name: "Price per question", exact: true }).fill("1.5");
	await page.getByRole("button", { name: "Save terms" }).click();
	await expect(page.getByTestId("terms-row-per_question_minor").getByTestId("custom-badge")).toBeVisible();
	await expect(page.getByTestId("terms-row-per_question_minor")).toContainText("Plan: ₹2.00");
	expect(JSON.stringify(billingStateFor(world).sent.at(-1)?.body)).toBe('{"overrides":{"per_question_minor":150}}');

	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await expectNoAxeViolations(page, "customer-billing");
});

test("the accounts list and the pricing page have no accessibility violations", async ({ page, context }, info) => {
	await installMockEngine(context, createWorld());
	await logIn(page, roleOf(info));
	await page.goto("/admin/billing");
	await expect(page.getByTestId("billing-accounts")).toBeVisible();
	await expectNoAxeViolations(page, "billing-accounts");
	await page.goto("/admin/billing?tab=pricing");
	await expect(page.getByTestId("pricing-card").first()).toBeVisible();
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await expectNoAxeViolations(page, "billing-pricing");
});

// BIL3 ledger
test("record a top-up on a customer and see it in their transactions and on the accounts list", async ({
	page,
	context,
}, info) => {
	const world = createWorld();
	await installMockEngine(context, world);
	await logIn(page, roleOf(info));

	await page.goto("/admin/billing");
	await expect(page.getByTestId("reconciliation-status")).toContainText("All good");
	await expectNoAxeViolations(page, "billing-books-check");
	await page.getByRole("link", { name: "Shree Labs" }).click();
	const ledger = page.getByTestId("customer-ledger");
	await expect(ledger.getByTestId("ledger-empty")).toBeVisible();
	await page.getByRole("button", { name: "Add a transaction" }).click();
	const dialog = page.getByRole("dialog", { name: "Add a transaction" });
	await dialog.getByLabel("Amount").fill("500");
	await dialog.getByLabel("Description for the customer").fill("UPI, 4 Oct");
	await dialog.getByRole("button", { name: "Add transaction" }).click();
	await expect(dialog).toBeHidden();
	await expect(ledger.getByTestId("ledger-balance")).toContainText("₹500.00");
	await expect(ledger.getByTestId("ledger-amount").first()).toHaveText("+₹500.00");
	await expect(ledger).toContainText("UPI, 4 Oct");
	expect(JSON.stringify(billingStateFor(world).sent.at(-1)?.body)).toContain('"amount_minor":50000');

	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await expectNoAxeViolations(page, "customer-ledger");

	await page.goto("/admin/billing");
	const row = page.getByRole("row", { name: /Shree Labs/ });
	await expect(row.getByTestId("account-balance")).toHaveText("₹500.00");
});
// /BIL3 ledger

// BIL5 add-ons
test("create a question pack, apply it to a customer and see their plan limit grow", async ({
	page,
	context,
}, info) => {
	const world = createWorld();
	// Shree Labs on a plan with 1,000 questions a month (the mock's Standard plan is unlimited).
	const starter = world.accounts.plans.find((p) => p.name === "Starter");
	const shree = world.tenants.find((t) => t.name === "Shree Labs");
	const meta = shree ? world.accounts.meta.get(shree.id) : undefined;
	if (!starter || !meta) throw new Error("the mock world has no Starter plan or no Shree Labs");
	meta.planId = starter.id;
	await installMockEngine(context, world);
	await logIn(page, roleOf(info));

	await page.goto("/admin/billing?tab=add-ons");
	await expect(page.getByTestId("add-ons-empty")).toBeVisible();
	await page.getByRole("button", { name: "New add-on" }).click();
	const dialog = page.getByRole("dialog", { name: "New add-on" });
	await dialog.getByLabel("Name").fill("500 extra questions");
	await dialog.getByLabel("Price", { exact: true }).fill("299");
	await dialog.getByLabel("Extra questions this month").fill("500");
	await expect(dialog.getByTestId("add-on-preview")).toContainText("+500 questions this month");
	await dialog.getByRole("button", { name: "Create add-on" }).click();
	await expect(dialog).toBeHidden();
	const card = page.getByRole("article", { name: /^500 extra questions/ });
	await expect(card.getByTestId("add-on-summary")).toHaveText("+500 questions this month");
	await expectNoAxeViolations(page, "billing-add-ons");

	await page.goto("/admin/billing");
	await page.getByRole("link", { name: "Shree Labs" }).click();
	await page.getByRole("tab", { name: "Overview" }).click();
	await expect(page.getByTestId("plan-usage")).toContainText("of 1,000");
	await page.getByRole("tab", { name: "Billing" }).click();
	await page.getByRole("button", { name: "Apply an add-on" }).click();
	const apply = page.getByRole("dialog", { name: /Apply an add-on to Shree Labs/ });
	await apply.getByLabel("Add-on").selectOption({ label: "500 extra questions (+500 questions this month, ₹299.00)" });
	await expect(apply.getByTestId("apply-effect")).toContainText("Questions a month 1,000 → 1,500 this month");
	await apply.getByRole("button", { name: "Apply add-on" }).click();
	await expect(apply).toBeHidden();
	await expect(page.getByTestId("grants-active")).toContainText("500 extra questions");
	expect(JSON.stringify(billingStateFor(world).sent.at(-1)?.body)).toContain('"payment":"external"');

	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await expectNoAxeViolations(page, "customer-add-ons");

	await page.getByRole("tab", { name: "Overview" }).click();
	await expect(page.getByTestId("plan-usage")).toContainText("of 1,500");
});
// /BIL5 add-ons
