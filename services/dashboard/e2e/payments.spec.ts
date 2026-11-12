import { expect, test } from "@playwright/test";
import { billingStateFor } from "./fixtures/mock-billing";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { seedAdminPayment } from "./fixtures/mock-payments";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * P1 online payments, on the mock engine (e2e/fixtures/mock-payments.ts): an owner adds ₹1,000 with
 * the tax details missing (the details form first, then the pay page, where a stand-in for
 * Razorpay's window pays), and the provider saves its keys, copies the webhook address and refunds
 * part of a payment. The real Razorpay window is the owner's smoke step in test mode.
 */
let world: World;

test.beforeEach(async ({ context }) => {
	world = createWorld();
	billingStateFor(world).billed = true;
	await installMockEngine(context, world);
	// Razorpay's script is never loaded here: this stand-in pays as soon as the window opens
	await context.addInitScript(() => {
		(window as unknown as { Razorpay: unknown }).Razorpay = class {
			options: { handler: (r: Record<string, string>) => void };
			constructor(options: { handler: (r: Record<string, string>) => void }) {
				this.options = options;
			}
			open() {
				setTimeout(
					() =>
						this.options.handler({
							razorpay_payment_id: "pay_Nq4Fk7Qa9ZZ",
							razorpay_order_id: "order_x",
							razorpay_signature: "signature-from-razorpay",
						}),
					50,
				);
			}
			on() {}
		};
	});
});

test("an owner adds ₹1,000: tax details first, then the pay page, then the money is in the balance", async ({
	page,
}, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's page");
	await logIn(page, "owner");
	await page.goto("/plan");
	await expect(page.getByRole("heading", { level: 1, name: "Plan & billing" })).toBeVisible();
	await page.getByRole("button", { name: "Add money" }).click();
	const dialog = page.getByTestId("add-money-dialog");
	await expect(dialog.getByText(/Add your billing details first/)).toBeVisible();
	await dialog.getByRole("button", { name: "₹1,000" }).click();
	await dialog.getByRole("button", { name: "Add billing details" }).click();

	const form = page.getByTestId("tax-details-form");
	await form.getByLabel("Business name").fill("Sharma Furniture");
	await form.getByLabel("Address", { exact: true }).fill("Shop 4, Paud Road, Kothrud");
	await form.getByLabel("City").fill("Pune");
	await form.getByLabel("State").selectOption({ label: "Maharashtra" });
	await form.getByRole("button", { name: "Save tax details" }).click();

	const again = page.getByTestId("add-money-dialog");
	await expect(again.getByTestId("add-money-preview")).toHaveText(
		"₹1,000.00 for your balance + ₹180.00 GST = ₹1,180.00 to pay",
	);
	await again.getByRole("button", { name: "Continue to payment" }).click();

	await expect(page).toHaveURL(/\/pay\?purpose=top_up&amount=100000/);
	await expect(page.getByTestId("pay-test-mode")).toBeVisible();
	await expect(page.getByTestId("pay-breakdown")).toContainText("₹1,180.00 to pay");
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await page.getByRole("button", { name: "Pay ₹1,180.00" }).click();
	await expect(page.getByTestId("pay-done")).toContainText("₹1,000.00 added");
	await expect(page.getByTestId("pay-done")).toContainText("Your balance is now ₹1,500.00");
	await expectNoAxeViolations(page, "pay");
	await fullShot(page, screenshotPath(info, "pay-done"));

	await page.getByRole("link", { name: "Back to Plan & billing" }).click();
	const rows = page.getByTestId("payment-row");
	await expect(rows).toHaveCount(1);
	await expect(rows.first()).toContainText("Includes ₹180.00 GST");
	await expect(rows.first()).not.toContainText(/fee/i);
});

test("an owner without online payment switched on sees no payment buttons", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's page");
	world.payments.enabled = false;
	await logIn(page, "owner");
	await page.goto("/plan");
	await expect(page.getByRole("heading", { level: 1, name: "Plan & billing" })).toBeVisible();
	await expect(page.getByTestId("balance-card")).toBeVisible();
	await expect(page.getByRole("button", { name: "Add money" })).toHaveCount(0);
});

test("the provider saves the keys, copies the webhook address and refunds part of a payment", async ({
	page,
}, info) => {
	test.skip(roleOf(info) !== "superadmin", "the provider's page");
	seedAdminPayment(world.payments, world.tenants[0]?.id ?? "t");
	await logIn(page, "superadmin");
	await page.goto("/admin/billing?tab=payments");
	const card = page.getByTestId("payment-settings");
	await expect(card).toBeVisible();
	await expect(card.getByTestId("payment-webhook-url")).toContainText("/api/v1/public/payments/razorpay/webhook");
	await card.getByLabel("Payment service").selectOption("razorpay");
	await card.getByLabel("Key id").fill("rzp_test_AbCdEf123456");
	await card.getByLabel("Key secret").fill("s3cr3t-value");
	await card.getByLabel("Webhook secret").fill("whsec_abc");
	await card.getByRole("button", { name: "Save payment settings" }).click();
	await expect(page.getByText("Payment settings saved")).toBeVisible();
	await expect(card.getByText("Saved", { exact: true })).toHaveCount(2);
	await expect(card).not.toContainText("s3cr3t-value");
	await card.getByRole("button", { name: "Test connection" }).click();
	await expect(card.getByTestId("payment-test-result")).toContainText("Connected (Test mode)");

	await page.getByRole("button", { name: "Open", exact: true }).first().click();
	const detail = page.getByTestId("payment-detail");
	await expect(detail.getByTestId("refundable")).toContainText("₹1,000.00");
	await detail.getByRole("button", { name: "Refund…" }).click();
	const refund = page.getByTestId("refund-dialog");
	await refund.getByLabel(/^Amount to take off the balance/).fill("500");
	await refund.getByLabel("Reason").fill("Customer cancelled");
	await refund.getByRole("button", { name: "Refund" }).click();
	await expect(page.getByText("Refund started")).toBeVisible();
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await expectNoAxeViolations(page, "admin-payments");
	expect(world.payments.sent.some((s) => s.path.endsWith("/refunds"))).toBe(true);
});
