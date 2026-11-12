import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * The operator's own workspace on the mock engine: the super admin switches into it, creates a
 * knowledge base the way any customer would, switches back and finds the customer list without it
 * (it sits in its own card, never in the lists, and can't be suspended). Super admin projects only.
 */

let world: World;

test.beforeEach(async ({ context }, info) => {
	test.skip(roleOf(info) !== "superadmin", "the operator view is the super admin's");
	world = createWorld();
	await installMockEngine(context, world);
});

async function pickView(page: Page, name: RegExp | string) {
	await page.getByTestId("scope-switcher").click();
	await page.getByRole("menuitem", { name }).click();
}

test("switch into the workspace, create a knowledge base, switch back: customers without the workspace", async ({
	page,
}) => {
	await logIn(page, "superadmin");

	// The operator view: the workspace is pinned in its own card, not in the customer list.
	await page.goto("/admin/customers?status=active");
	const card = page.getByTestId("operator-card");
	await expect(card).toBeVisible();
	await expect(card.getByTestId("operator-badge")).toHaveText("Your workspace");
	await expect(card.getByText("Internal")).toBeVisible();
	await expect(card.getByRole("button", { name: /Suspend|Decline|Delete/ })).toHaveCount(0);
	const table = page.getByRole("table", { name: "Active customers" });
	await expect(table.getByRole("rowheader")).toHaveCount(4);
	await expect(table.getByText("Sahayak AI")).toHaveCount(0);
	await expect(page.getByRole("tab", { name: "Active, 4" })).toBeVisible();
	await expectNoAxeViolations(page, "customers with workspace card");
	await fullShot(page, screenshotPath(test.info(), "operator-card"));

	// Its page: the badge and no Suspend, with the reason.
	await card.getByRole("link", { name: "Details of Sahayak AI" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Sahayak AI" })).toBeVisible();
	await page.getByRole("tab", { name: "Danger zone" }).click();
	await expect(page.getByText(/Suspend and decline aren't offered for your own workspace/)).toBeVisible();
	await expect(page.getByText("Suspend this customer")).toHaveCount(0);
	await expect(page.getByRole("button", { name: "Delete customer" })).toBeVisible();

	// Switch into the workspace: the tenant view, its own Home, no admin pages.
	await pickView(page, /Sahayak AI workspace \(yours\)/);
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	await expect(page.getByRole("heading", { name: "Set up your first assistant" })).toBeVisible();
	await expect(page.getByTestId("scope-switcher")).toHaveAccessibleName("Switch view: Sahayak AI workspace");
	if (!isPhone(page)) {
		const nav = page.getByRole("navigation", { name: "Main" });
		await expect(nav.getByRole("link", { name: "Team" })).toBeVisible();
		await expect(nav.getByRole("link", { name: /^Customers/ })).toHaveCount(0);
	}
	await page.goto("/admin/customers");
	await expect(page.getByRole("heading", { name: /not available/i })).toBeVisible();
	await expectNoAxeViolations(page, "workspace admin page not available");

	// Create the first knowledge base like any customer would.
	await page.goto("/kbs");
	await page.getByRole("button", { name: "New knowledge base" }).first().click();
	const dialog = page.getByRole("dialog", { name: "New knowledge base" });
	await dialog.getByLabel("Name").fill("Our landing-page assistant");
	await dialog.getByRole("button", { name: "Create knowledge base" }).click();
	await expect(page.getByRole("heading", { name: "Our landing-page assistant is ready" })).toBeVisible();
	await page.getByRole("dialog").getByRole("button", { name: "Done" }).click();
	await expect(page.getByRole("link", { name: "Our landing-page assistant" })).toBeVisible();
	await fullShot(page, screenshotPath(test.info(), "workspace-kbs"));

	// Back to the operator view: the customers again, still without the workspace.
	await pickView(page, /Operator view/);
	await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
	await expect(page.getByTestId("scope-switcher")).toHaveAccessibleName("Switch view: Operator view");
	await page.goto("/admin/customers?status=active");
	await expect(page.getByTestId("operator-card")).toBeVisible();
	await expect(page.getByTestId("operator-card")).toContainText("1 of ∞");
	const customers = page.getByRole("table", { name: "Active customers" });
	await expect(customers.getByRole("rowheader")).toHaveCount(4);
	await expect(customers.getByText("Sahayak AI")).toHaveCount(0);
	// Suspend is offered for customers, never for the workspace.
	await expect(customers.getByRole("button", { name: /^Suspend / })).toHaveCount(4);
	await expect(page.getByTestId("operator-card").getByRole("button", { name: /Suspend/ })).toHaveCount(0);
	// The knowledge base shows up in the operator's all-knowledge-bases list, under the workspace.
	await page.goto("/kbs");
	await expect(page.getByRole("link", { name: "Our landing-page assistant" })).toBeVisible();
});

test("a fresh engine: knowledge bases lead to 'Add your first customer', with your own workspace one click away", async ({
	page,
}) => {
	// A new engine: only the operator's own workspace exists.
	world.tenants = world.tenants.filter((t) => t.isOperator);
	world.kbs.items = [];
	await logIn(page, "superadmin");
	await page.goto("/kbs");
	await expect(page.getByRole("heading", { name: "Add your first customer" })).toBeVisible();
	await expect(page.getByRole("button", { name: "Or use your own workspace" })).toBeVisible();
	await expectNoAxeViolations(page, "first customer prompt");
	await fullShot(page, screenshotPath(test.info(), "first-customer"));

	await page.getByRole("link", { name: "Add your first customer" }).click();
	await expect(page.getByRole("dialog", { name: "Add a customer" })).toBeVisible();
	await page.getByRole("button", { name: "Cancel" }).click();

	await page.goto("/");
	const list = page.getByRole("region", { name: "Get started" });
	await expect(list.getByTestId("step-admin-workspace")).toHaveAttribute("data-state", "done");
	await expect(list.getByTestId("step-admin-kb")).toHaveAttribute("data-state", "todo");
	await list.getByRole("button", { name: "Switch and create" }).click();
	await expect(page.getByRole("dialog", { name: "New knowledge base" })).toBeVisible();
});

test("plans: Internal is locked, Pilot is an ordinary plan", async ({ page }) => {
	await logIn(page, "superadmin");
	await page.goto("/admin/plans");
	const internal = page.getByRole("article", { name: /^Internal/ });
	await expect(internal.getByTestId("plan-internal-badge")).toHaveText("Locked");
	await expect(internal.getByRole("button", { name: /More actions/ })).toHaveCount(0);
	await expect(internal.getByRole("button", { name: "Internal is locked" })).toHaveAttribute("aria-disabled", "true");
	const pilot = page.getByRole("article", { name: /^Pilot/ });
	await expect(pilot.getByRole("button", { name: "Edit Pilot" })).toBeEnabled();
	await expectNoAxeViolations(page, "plans with internal");
	await fullShot(page, screenshotPath(test.info(), "plans-internal"));
});
