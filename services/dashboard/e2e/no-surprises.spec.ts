import { expect, type Page, test } from "@playwright/test";
import { billingStateFor } from "./fixtures/mock-billing";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * TV3 no surprises, on the mock engine (e2e/fixtures/mock-billing.ts): the owner sees the next
 * renewal and where the month is heading and sets a monthly spending cap; the provider announces a
 * price rise (scheduled, with notice) and the owner then sees it coming on the Plan page.
 * Indian demo data: Shree Labs on the Standard plan.
 */
let world: World;

/** Standard has one price (Rs 999 a month, Rs 2 a question), the workspace is billed. */
function seedPricing(w: World) {
	const planId = w.accounts.meta.get(w.tenants.find((t) => t.name === "Shree Labs")?.id ?? "")?.planId ?? "";
	billingStateFor(w).versions.set(planId, [
		{
			plan_id: planId,
			version: 1,
			is_current: true,
			created_at: "2026-09-10T10:00:00Z",
			created_by: "admin key",
			currency: "INR",
			subscription_fee_minor: 99_900,
			billing_period: "monthly",
			trial_days: 0,
			usage_mode: "per_question",
			per_question_minor: 200,
			markup_bp: 0,
			byok_mode: "free",
			byok_per_question_minor: 0,
			byok_counts_toward_caps: true,
			overage: "block",
			overage_mode: "per_question",
			overage_per_question_minor: 0,
			payment_type: "prepaid",
			credit_limit_minor: 0,
			grace_minor: 0,
			included_credit_minor: 0,
			included_credit_expires: true,
			alert_thresholds_pct: [20, 5, 0],
			members_see_money: false,
		},
	]);
	billingStateFor(w).billed = true;
}

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
	seedPricing(world);
	await installMockEngine(context, world);
});

test("an owner sees the renewal and the month's pace, and sets a spending cap", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the owner's journey");
	await logIn(page, "owner");
	await page.goto("/plan");
	await expect(page.getByTestId("renewal-line")).toHaveText("Renews on 1 November 2026 for ₹999");
	await expect(page.getByTestId("projection-line")).toHaveText("About ₹3,000 this month at this pace");
	const card = page.getByTestId("spend-cap-card");
	await expect(card).toContainText("₹1,200 spent on usage this month");

	await card.getByTestId("spend-cap-edit").click();
	await card.getByLabel("Cap in rupees").fill("9,000");
	await card.getByRole("button", { name: "Save" }).click();
	await expect(card.getByText(/above the highest cap .* allows \(₹5,000\)/)).toBeVisible();
	expect(billingStateFor(world).sent.filter((s) => s.path === "/api/v1/billing/spend-cap")).toHaveLength(0);

	await card.getByLabel("Cap in rupees").fill("1,400");
	await card.getByRole("button", { name: "Save" }).click();
	await expect(card.getByTestId("spend-cap-line")).toHaveText("₹1,200 of ₹1,400 this month");
	await expect(card.getByTestId("spend-cap-state")).toHaveText("You have used most of your cap.");
	expect(billingStateFor(world).cap).toBe(140_000);
	await checkPage(page, "plan-no-surprises");
});

test("a member reads the cap but cannot change it", async ({ page }, info) => {
	test.skip(roleOf(info) !== "member", "the member's view");
	billingStateFor(world).cap = 140_000;
	await logIn(page, "member");
	await page.goto("/plan");
	// a member sees money only when the plan allows it: the card is absent or read-only
	await expect(page.getByTestId("spend-cap-edit")).toHaveCount(0);
});

test("the provider announces a raise, and the owner sees it coming", async ({ page, browser }, info) => {
	test.skip(roleOf(info) !== "owner", "needs both sides: the owner's view with the provider in a second window");
	const admin = await browser.newContext({ viewport: { width: 1440, height: 900 }, baseURL: "http://127.0.0.1:5173" });
	await installMockEngine(admin, world);
	const adminPage = await admin.newPage();
	await logIn(adminPage, "superadmin");

	await adminPage.goto("/admin/billing?tab=pricing");
	const plan = adminPage.getByRole("article", { name: /^Standard/ });
	await plan.getByRole("button", { name: "Edit pricing" }).click();
	const dialog = adminPage.getByRole("dialog", { name: "Pricing for Standard" });
	await dialog.getByLabel("Subscription fee").fill("1299");
	await dialog.getByRole("button", { name: "Save pricing" }).click();

	const schedule = adminPage.getByTestId("schedule-dialog");
	await expect(schedule).toBeVisible();
	await expect(schedule.getByTestId("schedule-lead")).toContainText("will pay more");
	const date = schedule.getByLabel("New price starts on");
	const earliest = (await date.getAttribute("min")) ?? "";
	// a day before the earliest is refused, nothing is sent
	await date.fill("2020-01-01");
	await schedule.getByRole("button", { name: "Announce and schedule" }).click();
	await expect(schedule.getByText(/Choose .* or a later day/)).toBeVisible();
	expect(billingStateFor(world).sent.filter((s) => s.method === "PUT")).toHaveLength(0);
	await date.fill(earliest);
	await schedule.getByRole("button", { name: "Announce and schedule" }).click();
	await expect(schedule).toBeHidden();
	await expect(plan.getByTestId("scheduled-chip")).toHaveText("Scheduled");
	const put = billingStateFor(world).sent.find((s) => s.method === "PUT");
	expect(put?.body).toMatchObject({ apply: "scheduled", effective_at: `${earliest}T00:00:00+05:30` });
	await admin.close();

	await logIn(page, "owner");
	await page.goto("/plan");
	const banner = page.getByTestId("price-changes");
	await expect(banner).toBeVisible();
	await expect(banner).toContainText("Plan fee: ₹999 → ₹1,299");
	await expect(banner).toContainText("Nothing changes until your next renewal");
	await checkPage(page, "plan-price-change");
});

test("a drop starts at once and is not scheduled", async ({ page }, info) => {
	test.skip(roleOf(info) !== "superadmin", "the provider's page");
	await logIn(page, "superadmin");
	await page.goto("/admin/billing?tab=pricing");
	const plan = page.getByRole("article", { name: /^Standard/ });
	await plan.getByRole("button", { name: "Edit pricing" }).click();
	const dialog = page.getByRole("dialog", { name: "Pricing for Standard" });
	await dialog.getByLabel("Subscription fee").fill("799");
	await dialog.getByRole("button", { name: "Save pricing" }).click();
	await expect(dialog).toBeHidden();
	await expect(page.getByTestId("schedule-dialog")).toHaveCount(0);
	await expect(plan.getByTestId("pricing-summary")).toContainText("₹799.00");
	expect(billingStateFor(world).sent.find((s) => s.method === "PUT")?.body).toMatchObject({ apply: "now" });
});
