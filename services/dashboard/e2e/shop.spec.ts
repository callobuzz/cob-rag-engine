import { expect, test } from "@playwright/test";
import { billingStateFor } from "./fixtures/mock-billing";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * P5 add-ons and service orders, on the mock engine (e2e/fixtures/mock-shop.ts): an owner buys
 * "Installation help" from the balance and sees the order as Requested; the provider moves it to
 * In progress with a note the owner then reads (the internal note never reaches the owner); an
 * owner on a plan without the allowance sees the own-key lock instead of the key form.
 */
const PATIENT = "7b221003-0000-4000-8000-000000000001";
let world: World;

test.beforeEach(async ({ context }) => {
	world = createWorld();
	billingStateFor(world).billed = true;
	billingStateFor(world).balance = world.shop.available; // what the shop can spend
	await installMockEngine(context, world);
});

test("an owner buys a service from the balance and sees the order", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's page");
	await logIn(page, "owner");
	await page.goto("/plan");
	await expect(page.getByRole("heading", { level: 1, name: "Plan & billing" })).toBeVisible();
	const shop = page.getByTestId("shop-card");
	await expect(shop).toBeVisible();
	await expect(shop.getByRole("heading", { name: "Your own AI key" })).toBeVisible();
	const help = shop.getByTestId("shop-item").filter({ hasText: "Installation help" });
	await expect(help.getByTestId("shop-price")).toContainText("₹1,999.00 + GST · ₹2,358.82 incl. GST");
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await expectNoAxeViolations(page, "shop-card");
	await fullShot(page, screenshotPath(info, "shop-owner"));

	await help.getByRole("button", { name: "Buy" }).click();
	const dialog = page.getByRole("dialog", { name: "Buy Installation help" });
	await dialog.getByLabel("Anything we should know?").fill("Please call before 11");
	await dialog.getByRole("button", { name: /from balance/ }).click();
	await expect(page.getByText("Done: Installation help")).toBeVisible();

	const orders = page.getByTestId("owner-orders");
	await expect(orders.getByTestId("order-status")).toHaveText("Requested");
	await expect(orders).toContainText("Please call before 11");
	expect(world.shop.bought).toHaveLength(1);
	expect(world.shop.available).toBe(500_000 - 199_900);
	await fullShot(page, screenshotPath(info, "shop-owner-ordered"));
});

test("an owner sees the provider's note on a service in progress, never the internal note", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's page");
	const shree = world.tenants.find((t) => t.name === "Shree Labs");
	world.shop.orders = [
		{
			id: "bbbbbbbb-0000-4000-8000-000000000001",
			tenant_id: shree?.id ?? "t",
			tenant_name: "Shree Labs",
			grant_id: "g",
			name: "Installation help",
			status: "in_progress",
			status_label: "In progress",
			owner_note: null,
			public_note: "We start on Monday",
			internal_note: "vendor quote 12k",
			includes_support_months: 3,
			support_until: null,
			support_request_id: null,
			created_at: "2026-10-06T10:00:00Z",
			updated_at: "2026-10-06T10:00:00Z",
			completed_at: null,
		},
	];
	await logIn(page, "owner");
	await page.goto("/plan");
	const orders = page.getByTestId("owner-orders");
	await expect(orders.getByTestId("order-status")).toHaveText("In progress");
	await expect(orders.getByTestId("order-note")).toContainText("We start on Monday");
	await expect(orders).not.toContainText("vendor quote");
});

test("the provider moves an order to In progress with a note", async ({ page }, info) => {
	test.skip(roleOf(info) !== "superadmin", "the provider's page");
	const shree = world.tenants.find((t) => t.name === "Shree Labs");
	world.shop.orders = [
		{
			id: "bbbbbbbb-0000-4000-8000-000000000001",
			tenant_id: shree?.id ?? "t",
			tenant_name: "Shree Labs",
			grant_id: "g",
			name: "Installation help",
			status: "requested",
			status_label: "Requested",
			owner_note: "Please call before 11",
			public_note: null,
			internal_note: null,
			includes_support_months: 3,
			support_until: null,
			support_request_id: null,
			created_at: "2026-10-06T10:00:00Z",
			updated_at: "2026-10-06T10:00:00Z",
			completed_at: null,
		},
	];
	await logIn(page, "superadmin");
	await page.goto("/admin/billing?tab=service-orders");
	const table = page.getByRole("table", { name: "Service orders" });
	await expect(table.getByText("Installation help")).toBeVisible();
	await expectNoAxeViolations(page, "service-orders-tab");
	await table.getByRole("button", { name: "Open" }).click();
	const sheet = page.getByTestId("order-sheet");
	await expect(sheet).toContainText("Please call before 11");
	await sheet.getByLabel("Note for the owner").fill("We start on Monday");
	await sheet.getByLabel("Internal note").fill("vendor quote 12k");
	await sheet.getByRole("button", { name: "Start work" }).click();
	await expect(sheet.getByText("In progress")).toBeVisible();
	expect(world.shop.orders[0]?.status).toBe("in_progress");
	expect(world.shop.orders[0]?.public_note).toBe("We start on Monday");
	await fullShot(page, screenshotPath(info, "service-orders-admin"));
});

test("an owner on a plan without the allowance sees the own-key lock, not the key form", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's page");
	billingStateFor(world).byok = { allowed: false, key_saved: false, source: null, plans_with_byok: ["Pro"] };
	await logIn(page, "owner");
	await page.goto(`/kbs/${PATIENT}?tab=model`);
	const lock = page.getByTestId("own-key-lock");
	await expect(lock).toBeVisible();
	await expect(lock).toContainText("Using your own AI key is part of Pro, or available as an add-on.");
	await expect(page.getByRole("button", { name: /^Set key|^Replace key/ })).toHaveCount(0);
	await expectNoAxeViolations(page, "own-key-lock");
	await fullShot(page, screenshotPath(info, "own-key-lock"));
});

test("a member sees the shop but cannot buy", async ({ page }, info) => {
	test.skip(roleOf(info) !== "member", "the member's page");
	await logIn(page, "member");
	await page.goto("/plan");
	const shop = page.getByTestId("shop-card");
	await expect(shop).toBeVisible();
	await expect(shop.getByRole("button", { name: "Buy" })).toHaveCount(0);
});
