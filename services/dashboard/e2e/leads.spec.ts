import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { createLeadsState, installLeadsMock, type LeadsState } from "./fixtures/mock-leads";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * C4: the leads inbox on the mock engine (e2e/fixtures/mock-leads.ts on top of mock-engine.ts),
 * across the README matrix. Indian demo data: Shree Labs, "Patient help".
 */
let state: LeadsState;

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth), name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

test.describe("leads inbox", () => {
	test.beforeEach(async ({ context, page }, info) => {
		const world = createWorld();
		await installMockEngine(context, world);
		state = createLeadsState(world);
		await installLeadsMock(context, world, state);
		await logIn(page, roleOf(info));
	});

	test("lists, filters by phone, edits a lead and shows its consent and chat", async ({ page }, info) => {
		const role = roleOf(info);
		await page.goto("/leads");
		await expect(page.getByRole("heading", { level: 1, name: "Leads" })).toBeVisible();
		await expect(page.getByRole("rowheader", { name: /Priya Kulkarni/ })).toBeVisible();
		await expect(page.getByRole("rowheader", { name: /Rohan Deshmukh/ })).toBeVisible();
		// Only the operator sees another customer's leads.
		if (role === "superadmin") await expect(page.getByRole("rowheader", { name: /Meena Joshi/ })).toBeVisible();
		else await expect(page.getByText("Meena Joshi")).toHaveCount(0);
		await checkPage(page, "leads-list");

		await page.getByLabel("Phone or e-mail").fill("98123 45678");
		await expect(page.getByRole("rowheader", { name: /Priya Kulkarni/ })).toHaveCount(0);
		await page.getByRole("button", { name: "Rohan Deshmukh", exact: true }).click();
		const drawer = page.getByRole("dialog", { name: /Rohan Deshmukh/ });
		await expect(drawer.getByText(/Agreed on 13 Oct 2026 to notice v1/)).toBeVisible();
		await drawer.getByLabel("Status").selectOption("won");
		await drawer.getByLabel("Notes").fill("Booked the home visit");
		await drawer.getByRole("button", { name: "Save" }).click();
		await expect(page.getByText("Lead updated")).toBeVisible();
		expect(state.patches.at(-1)).toEqual({ status: "won", notes: "Booked the home visit" });
		await checkPage(page, "leads-drawer");
	});

	test("shows the qualification as words and filters to qualified leads", async ({ page }) => {
		await page.goto("/leads");
		const priya = page.getByRole("rowheader", { name: /Priya Kulkarni/ });
		await expect(priya).toBeVisible();
		await expect(page.getByRole("row", { name: /Priya Kulkarni/ }).getByText("Qualified")).toBeVisible();
		await expect(page.getByRole("row", { name: /Rohan Deshmukh/ }).getByText("Not qualified")).toBeVisible();
		await page.getByLabel("Qualification").selectOption("qualified");
		await expect(page).toHaveURL(/qualification=qualified/);
		await expect(page.getByRole("rowheader", { name: /Rohan Deshmukh/ })).toHaveCount(0);
		await expect(priya).toBeVisible();
		await checkPage(page, "leads-qualified");
	});

	test("the chat a lead came from reads in order", async ({ page }) => {
		await page.goto("/leads");
		await page.getByRole("button", { name: "Priya Kulkarni", exact: true }).click();
		const drawer = page.getByRole("dialog", { name: /Priya Kulkarni/ });
		const chat = drawer.getByRole("region", { name: "The chat" });
		await expect(chat.getByText("Do I need to fast for a lipid profile?")).toBeVisible();
		await expect(chat.getByText("10–12 hours")).toBeVisible();
	});

	test("export and delete are for owners; a member sees them locked", async ({ page }, info) => {
		const role = roleOf(info);
		await page.goto("/leads");
		const exportBtn = page.getByRole("button", { name: "Export CSV" });
		if (role === "member") {
			await expect(exportBtn).toHaveAttribute("aria-disabled", "true");
		} else {
			const download = page.waitForEvent("download");
			await exportBtn.click();
			expect((await download).suggestedFilename()).toMatch(/^leads-\d{4}-\d{2}-\d{2}\.csv$/);
			expect(state.exports).toBe(1);
		}
		await page.getByRole("button", { name: "Rohan Deshmukh", exact: true }).click();
		const drawer = page.getByRole("dialog", { name: /Rohan Deshmukh/ });
		const del = drawer.getByRole("button", { name: "Delete lead" });
		if (role === "member") {
			await expect(del).toHaveAttribute("aria-disabled", "true");
			return;
		}
		await del.click();
		const confirm = page.getByRole("alertdialog");
		await expect(confirm.getByRole("button", { name: "Delete lead" })).toBeDisabled();
		await confirm.getByRole("textbox").fill("delete");
		await confirm.getByRole("button", { name: "Delete lead" }).click();
		await expect(page.getByText("Lead deleted")).toBeVisible();
		await expect(page.getByRole("rowheader", { name: /Rohan Deshmukh/ })).toHaveCount(0);
		expect(state.deleted).toEqual(["6b0c0000-0000-4000-8000-000000000002"]);
	});
});

test.describe("handoffs", () => {
	test.beforeEach(async ({ context, page }, info) => {
		const world = createWorld();
		await installMockEngine(context, world);
		state = createLeadsState(world);
		await installLeadsMock(context, world, state);
		await logIn(page, roleOf(info));
	});

	test("lists who was offered a person, opens one and marks it handled", async ({ page }, info) => {
		const role = roleOf(info);
		await page.goto("/leads?tab=handoffs");
		const tab = page.getByRole("tab", { name: /^Handoffs/ });
		await expect(tab).toHaveAttribute("aria-selected", "true");
		// Another customer's handoff is the operator's to see, never a tenant's.
		await expect(tab).toHaveText(role === "superadmin" ? "Handoffs (2 new)" : "Handoffs (1 new)");
		await expect(page.getByRole("button", { name: "WhatsApp" })).toBeVisible();
		await expect(page.getByRole("button", { name: "Callback" })).toBeVisible();
		if (role === "superadmin") await expect(page.getByRole("button", { name: "Call", exact: true })).toBeVisible();
		else await expect(page.getByRole("button", { name: "Call", exact: true })).toHaveCount(0);
		await checkPage(page, "handoffs-list");

		await page.getByRole("button", { name: "WhatsApp" }).click();
		const drawer = page.getByRole("dialog", { name: /Handoff from/ });
		await expect(drawer.getByText(/home collection was cancelled/)).toBeVisible();
		await expect(drawer.getByRole("link", { name: "+91 98765 43210" })).toHaveAttribute("href", "tel:+919876543210");
		await expect(drawer.getByText("refund chahiye")).toBeVisible();
		await checkPage(page, "handoffs-drawer");
		await drawer.getByRole("button", { name: "Mark handled" }).click();
		await expect(page.getByText("Handoff marked handled")).toBeVisible();
		expect(state.handoffPatches.at(-1)).toEqual({ status: "handled" });
		await expect(drawer.getByRole("button", { name: "Mark as new" })).toBeVisible();
	});

	test("filters by status and a member may mark a handoff as new again", async ({ page }, info) => {
		await page.goto("/leads?tab=handoffs");
		await page.getByRole("radio", { name: "Handled" }).click();
		await expect(page).toHaveURL(/hstatus=handled/);
		await expect(page.getByRole("button", { name: "Callback" })).toBeVisible();
		await expect(page.getByRole("button", { name: "WhatsApp" })).toHaveCount(0);
		if (roleOf(info) !== "member") return;
		await page.getByRole("button", { name: "Callback" }).click();
		await page
			.getByRole("dialog", { name: /Handoff from/ })
			.getByRole("button", { name: "Mark as new" })
			.click();
		await expect(page.getByText("Handoff marked as new")).toBeVisible();
	});
});

// TV2 reference
test.describe("find a reference", () => {
	test.beforeEach(async ({ context, page }, info) => {
		const world = createWorld();
		await installMockEngine(context, world);
		state = createLeadsState(world);
		await installLeadsMock(context, world, state);
		await logIn(page, roleOf(info));
	});

	test("typing a quoted reference opens that chat", async ({ page }) => {
		await page.goto("/leads?tab=handoffs");
		await expect(page.getByRole("cell", { name: "SD-4F7K" })).toBeVisible();
		await page.getByLabel("Find a reference").fill("sd 4f7k");
		await page.getByRole("button", { name: "Find" }).click();
		const drawer = page.getByRole("dialog", { name: /Handoff from/ });
		await expect(drawer.getByText("SD-4F7K")).toBeVisible();
		await expect(drawer.getByText(/home collection was cancelled/)).toBeVisible();
	});

	test("an unknown reference says so", async ({ page }) => {
		await page.goto("/leads?tab=handoffs");
		await page.getByLabel("Find a reference").fill("SD-ZZZZ");
		await page.getByRole("button", { name: "Find" }).click();
		await expect(page.getByText("No conversation with that reference in your knowledge bases.")).toBeVisible();
	});
});
// /TV2 reference
