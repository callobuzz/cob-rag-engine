import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * TV4: the value report on the mock engine (e2e/fixtures/mock-value.ts), across the README matrix:
 * the Home card, the full report with its formula, changing the owner's values, the printable page,
 * a member's read-only view and the deployer's Value tab. Indian demo data: Shree Dental Clinic, Pune.
 */
let world: World;

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth), name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

test.beforeEach(async ({ context }) => {
	world = createWorld();
	await installMockEngine(context, world);
});

test("an owner: Home card, the full report, changing the values and the printable page", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role !== "owner", "the owner's journey");
	await logIn(page, role);

	const card = page.getByTestId("home-value");
	await expect(card).toBeVisible();
	await expect(page.getByTestId("home-value-total")).toHaveText("₹37,050");
	await expect(page.getByText("Estimated, based on your settings")).toBeVisible();
	await expect(page.getByTestId("home-value-set")).toBeVisible();

	await card.getByRole("link", { name: "See the full report" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Value report" })).toBeVisible();
	await expect(page.getByTestId("value-total")).toHaveText("₹37,050");
	await expect(page.getByTestId("formula-leads")).toHaveText("9 qualified leads × ₹1,500 = ₹13,500");
	await expect(page.getByTestId("formula-handoffs")).toHaveText("23 requests to talk to a person × ₹800 = ₹18,400");
	await expect(page.getByTestId("formula-time")).toHaveText(
		"412 questions answered × 3 min saved = 20 h 36 min × ₹250/h = ₹5,150",
	);
	await expect(page.getByTestId("unanswered")).toContainText("do you take cards");
	await checkPage(page, "value");

	await page.getByTestId("value-change").click();
	const dialog = page.getByRole("dialog", { name: "Your values" });
	const lead = dialog.getByLabel("What a lead is worth");
	await lead.fill("2,000");
	await dialog.getByRole("button", { name: "Save values" }).click();
	await expect(dialog).toBeHidden();
	await expect(page.getByTestId("value-total")).toHaveText("₹41,550");
	expect(world.value.puts).toHaveLength(1);
	expect(world.value.puts[0]).toMatchObject({ lead_value_minor: 200_000, handoff_value_minor: 80_000 });
	await expect(page.getByText(/suggested ones are used/)).toHaveCount(0);

	const popup = page.waitForEvent("popup");
	await page.getByTestId("value-print").click();
	const tab = await popup;
	await expect(tab.getByText("Estimated value, based on your settings")).toBeVisible();
});

test("the month picker and a month the engine refuses", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role !== "owner", "the owner's page");
	await logIn(page, role);
	await page.goto("/value");
	await expect(page.getByTestId("value-total")).toBeVisible();
	await page.getByLabel("Month").selectOption("2026-09");
	await expect(page).toHaveURL(/month=2026-09/);
	await expect(page.getByRole("heading", { name: "Estimated value" })).toBeVisible();
	await page.goto("/value?month=2026-12");
	await expect(page.getByText(/not happened yet/)).toBeVisible();
});

test("a member reads the report but cannot change the values", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role !== "member", "the member's view");
	await logIn(page, role);
	await page.goto("/value");
	await expect(page.getByTestId("value-total")).toBeVisible();
	await expect(page.getByTestId("value-change")).toHaveCount(0);
	await expect(page.getByText("Only an owner can change these values.")).toBeVisible();
});

test("the deployer reads a business's report on its Value tab, read only", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role !== "superadmin", "the operator's view");
	await logIn(page, role);
	const shree = world.tenants.find((t) => t.name === "Shree Labs");
	await page.goto(`/admin/customers/${shree?.id}?tab=value`);
	const tab = page.getByTestId("admin-value");
	await expect(tab).toBeVisible();
	await expect(tab.getByTestId("value-total")).toHaveText("₹37,050");
	await expect(tab.getByRole("link", { name: "Add an answer" })).toHaveCount(0);
	await expect(page.getByTestId("value-change")).toHaveCount(0);
});
