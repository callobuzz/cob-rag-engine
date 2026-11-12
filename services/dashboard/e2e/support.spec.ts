import { expect, type Page, test } from "@playwright/test";
import { createAdminState, installAdminMock } from "./fixtures/mock-admin";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * TV3 support requests, on the mock engine (e2e/fixtures/mock-support.ts): a business opens a
 * request from "Get help" (COB first, then "Contact"), the provider answers from the inbox, the
 * business sees the reply and closes it. Indian demo data: Shree Labs writes to Sahayak AI.
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

test("a business writes to its provider, reads the reply and closes the request", async ({ page, browser }, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's journey, with the provider in a second window");
	await logIn(page, "owner");

	// COB first: its panel offers the way on, with the page the person was on
	await page.goto("/plan");
	await page.getByTestId("get-help").click();
	await page.getByTestId("cob-contact-provider").getByRole("link", { name: "Contact Sahayak AI" }).click();
	await expect(page).toHaveURL(/\/support\?new=1&page=%2Fplan/);
	await expect(page.getByTestId("support-sla")).toHaveText("Usually replies within 4 hours.");
	const form = page.getByTestId("support-form");
	await form.getByRole("button", { name: "Send request" }).click();
	await expect(form.getByText("Write at least 3 characters.")).toBeVisible();
	await form.getByLabel("Subject").fill("WhatsApp button not showing");
	await form.getByLabel("What happened?").fill("Since yesterday the widget has no button.");
	await form.getByRole("button", { name: "Send request" }).click();
	await expect(page.getByTestId("support-thread")).toContainText("WhatsApp button not showing");
	await expect(page.getByTestId("support-thread").getByTestId("request-status")).toContainText(
		"Waiting for Sahayak AI",
	);
	expect(world.support.sent[0]?.body).toMatchObject({ page: "/plan", subject: "WhatsApp button not showing" });
	await checkPage(page, "support");

	// the provider answers from the inbox
	const provider = await browser.newContext({
		viewport: { width: 1440, height: 900 },
		baseURL: "http://127.0.0.1:5173",
	});
	await installMockEngine(provider, world);
	const inbox = await provider.newPage();
	await logIn(inbox, "superadmin");
	await inbox.goto("/admin/support");
	await expect(inbox.getByTestId("stat-open")).toHaveText("1");
	await inbox.getByTestId("admin-support-row").first().click();
	const thread = inbox.getByTestId("admin-support-thread");
	await expect(thread.getByTestId("support-context")).toContainText("Starter");
	await thread.getByLabel("Your reply").fill("Fixed: your plan now includes the widget.");
	await thread.getByRole("button", { name: "Send reply" }).click();
	await expect(thread.getByTestId("message-deployer")).toContainText("Fixed: your plan now includes the widget.");
	await expect(inbox.getByTestId("stat-open")).toHaveText("0");
	await provider.close();

	// the business sees the reply and closes it
	await page.reload();
	const reply = page.getByTestId("message-deployer");
	await expect(reply).toContainText("Fixed: your plan now includes the widget.");
	await expect(page.getByTestId("support-thread").getByTestId("request-status")).toContainText("Answered");
	await page.getByRole("button", { name: "Mark as done" }).click();
	await expect(page.getByText(/This request is closed/)).toBeVisible();
	expect(world.support.rows[0]?.status).toBe("closed");
});

test("when the provider turned support off the form is replaced by contact details", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's view");
	world.support.enabled = false;
	await logIn(page, "owner");
	await page.goto("/support");
	await expect(page.getByTestId("support-disabled")).toContainText("Sahayak AI has switched support requests off.");
	await expect(page.getByTestId("support-new")).toHaveCount(0);
});

test("the inbox is the provider's: it lists requests with the numbers above", async ({ page }, info) => {
	test.skip(roleOf(info) !== "superadmin", "the provider's page");
	await logIn(page, "superadmin");
	await page.goto("/admin/support");
	await expect(page.getByRole("heading", { level: 1, name: "Support inbox" })).toBeVisible();
	await expect(page.getByTestId("admin-support-empty")).toBeVisible();
	await expect(page.getByTestId("support-stats")).toBeVisible();
	await checkPage(page, "admin-support");
});

test("the price list: pick plans, preview in a sandboxed frame, make and remove a link", async ({
	page,
	context,
}, info) => {
	test.skip(roleOf(info) !== "superadmin", "the provider's page");
	await installAdminMock(context, world, createAdminState(world)); // the engine settings page
	await logIn(page, "superadmin");
	await page.goto("/admin/price-sheet");
	await expect(page.getByRole("heading", { level: 1, name: "Price list" })).toBeVisible();
	await expect(page.getByTestId("price-sheet-preview")).toHaveAttribute("sandbox", "");
	await page.getByTestId("price-sheet-create").click();
	await expect(page.getByTestId("price-sheet-url")).toContainText("/api/v1/public/price-sheet/");
	await page.getByTestId("price-sheet-remove").click();
	await page.getByRole("button", { name: "Remove the link" }).click();
	await expect(page.getByTestId("price-sheet-create")).toBeVisible();
	await checkPage(page, "admin-price-sheet");
});
