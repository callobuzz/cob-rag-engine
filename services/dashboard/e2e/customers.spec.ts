import { type Browser, expect, type Page, test } from "@playwright/test";
import { ACCOUNTS, createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * D7 Customers and Plans (super admin) on the mock engine (e2e/fixtures/mock-accounts.ts), across
 * the README matrix; tenant projects check the pages are not available. The same journeys
 * against the running stack are pending at the bottom of the file.
 */

let world: World;

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		const width = await page.evaluate(() => document.documentElement.scrollWidth);
		expect(width, name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

/** Email + password sign-in for any account of the mock world. */
async function logInAs(page: Page, email: string, password: string) {
	await page.goto("/login");
	await page.getByLabel("Email").fill(email);
	await page.getByLabel("Password", { exact: true }).fill(password);
	await page.getByRole("button", { name: "Log in" }).click();
}

async function secondPage(browser: Browser): Promise<Page> {
	const context = await browser.newContext();
	await installMockEngine(context, world);
	return context.newPage();
}

const rowOf = (page: Page, name: string) =>
	page.getByRole("row").filter({ has: page.getByRole("rowheader", { name: new RegExp(name) }) });

test.beforeEach(async ({ context }, info) => {
	world = createWorld();
	world.signUp("Anand Dental Care", "Anand Joshi", "dr.anand@anandental.in");
	await installMockEngine(context, world);
	if (info.title.includes("accept")) {
		await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: "http://127.0.0.1:5173" });
	}
});

test.describe("customers (super admin)", () => {
	test.beforeEach(({ browserName: _browser }, info) => {
		test.skip(roleOf(info) !== "superadmin", "super admin pages");
	});

	test("pending sign-up with badge → activate on Starter with a note → active", async ({ page }) => {
		await logIn(page, "superadmin");
		if (!isPhone(page)) {
			await expect(
				page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: /^Customers\s*2 pending sign-ups$/ }),
			).toBeVisible();
		}
		await page.goto("/admin/customers");
		await expect(page.getByRole("tab", { name: "Pending, 2" })).toHaveAttribute("aria-selected", "true");
		const anand = rowOf(page, "Anand Dental Care");
		await expect(anand).toContainText("dr.anand@anandental.in");
		await checkPage(page, "customers-pending");

		await anand.getByRole("button", { name: "Activate Anand Dental Care" }).click();
		const dialog = page.getByRole("dialog", { name: "Activate Anand Dental Care" });
		await expect(dialog.getByLabel("Plan")).toHaveValue(
			world.accounts.plans.find((p) => p.name === "Starter")?.id ?? "",
		);
		await dialog.getByLabel("Note for your records").fill("Paid ₹3,000 via UPI on 29 Sep");
		await expectNoAxeViolations(page, "activate dialog");
		await fullShot(page, screenshotPath(test.info(), "customers-activate"));
		await dialog.getByRole("button", { name: "Activate" }).click();
		await expect(dialog).toBeHidden();
		await expect(page.getByText("Anand Dental Care is active")).toBeVisible();
		await expect(page.getByRole("tab", { name: "Pending, 1" })).toBeVisible();
		await page.getByRole("tab", { name: /^Active/ }).click();
		const active = rowOf(page, "Anand Dental Care");
		await expect(active).toContainText("Starter");
		await expect(active).toContainText("Paid ₹3,000 via UPI on 29 Sep");
		expect(world.tenants.find((t) => t.name === "Anand Dental Care")?.status).toBe("active");
		expect(world.accounts.mail.some((m) => m.to === "dr.anand@anandental.in" && /You're in/.test(m.subject))).toBe(
			true,
		);
		await checkPage(page, "customers-active");
	});

	test("suspend with the typed name → the owner lands on the paused page → reactivate → works again", async ({
		page,
		browser,
	}) => {
		test.setTimeout(120_000);
		await logIn(page, "superadmin");
		await page.goto("/admin/customers?status=active");
		await rowOf(page, "Mehta Opticals").getByRole("button", { name: "Suspend Mehta Opticals" }).click();
		const dialog = page.getByRole("alertdialog", { name: "Suspend Mehta Opticals?" });
		const typed = dialog.getByLabel(/Type Mehta Opticals to confirm/);
		await typed.fill("mehta opticals");
		await expect(dialog.getByRole("button", { name: "Suspend" })).toBeDisabled();
		await typed.fill("Mehta Opticals");
		await dialog.getByLabel("Reason (optional)").fill("Payment overdue since 15 Sep");
		await expectNoAxeViolations(page, "suspend dialog");
		await dialog.getByRole("button", { name: "Suspend" }).click();
		await expect(dialog).toBeHidden();
		await expect(page.getByRole("tab", { name: "Suspended, 1" })).toBeVisible();
		expect(world.accounts.sent.at(-1)).toMatchObject({
			method: "POST",
			body: { confirm_name: "Mehta Opticals", reason: "Payment overdue since 15 Sep" },
		});

		const owner = await secondPage(browser);
		await logInAs(owner, ACCOUNTS.mehta.email, ACCOUNTS.mehta.password);
		await expect(owner.getByRole("heading", { name: "Mehta Opticals is paused" })).toBeVisible();

		await page.getByRole("tab", { name: /^Suspended/ }).click();
		await expect(rowOf(page, "Mehta Opticals").getByRole("link", { name: "History of Mehta Opticals" })).toBeVisible();
		await checkPage(page, "customers-suspended");
		await rowOf(page, "Mehta Opticals").getByRole("button", { name: "Reactivate Mehta Opticals" }).click();
		await page
			.getByRole("alertdialog", { name: "Reactivate Mehta Opticals?" })
			.getByRole("button", { name: "Reactivate" })
			.click();
		await expect(page.getByText("Mehta Opticals reactivated")).toBeVisible();

		await owner.getByRole("button", { name: "Log out" }).click();
		await logInAs(owner, ACCOUNTS.mehta.email, ACCOUNTS.mehta.password);
		await expect(owner.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
		await owner.context().close();
	});

	test("delete with the typed name", async ({ page }) => {
		await logIn(page, "superadmin");
		await page.goto("/admin/customers?status=active");
		await rowOf(page, "Sai Tutorials").getByRole("button", { name: "Delete Sai Tutorials" }).click();
		const dialog = page.getByRole("alertdialog", { name: "Delete Sai Tutorials?" });
		await expect(dialog).toContainText("This cannot be undone.");
		const go = dialog.getByRole("button", { name: "Delete customer" });
		await dialog.getByLabel(/Type Sai Tutorials to confirm/).fill("Sai tutorials");
		await expect(go).toBeDisabled();
		await dialog.getByLabel(/Type Sai Tutorials to confirm/).fill("Sai Tutorials");
		await expectNoAxeViolations(page, "delete dialog");
		await go.click();
		await expect(page.getByText("Sai Tutorials deleted")).toBeVisible();
		await expect(rowOf(page, "Sai Tutorials")).toHaveCount(0);
		expect(world.tenants.some((t) => t.name === "Sai Tutorials")).toBe(false);
	});

	test("add customer Vidya Classes → invite the owner → accept the link in another browser", async ({
		page,
		browser,
	}) => {
		test.setTimeout(120_000);
		await logIn(page, "superadmin");
		await page.goto("/admin/customers?status=active");
		await page.getByRole("button", { name: "Add customer" }).click();
		let dialog = page.getByRole("dialog", { name: "Add a customer" });
		await dialog.getByLabel("Business name").fill("Vidya Classes");
		await dialog.getByLabel("Description").fill("Tuition classes, Hadapsar");
		await expectNoAxeViolations(page, "add customer");
		await dialog.getByRole("button", { name: "Add customer" }).click();
		dialog = page.getByRole("dialog", { name: "Vidya Classes is ready" });
		await expect(dialog.getByText(/^tk_/)).toBeVisible();
		await fullShot(page, screenshotPath(test.info(), "customers-add-key"));
		await dialog.getByRole("button", { name: "Next: invite the owner" }).click();
		dialog = page.getByRole("dialog", { name: "Invite the owner of Vidya Classes" });
		await dialog.getByLabel("Owner's email").fill("admin@vidyaclasses.in");
		await dialog.getByRole("button", { name: "Create invitation" }).click();
		dialog = page.getByRole("dialog", { name: "Invitation for admin@vidyaclasses.in" });
		await expect(dialog.getByText(/Email isn't set up on this engine, so nothing was sent/)).toBeVisible();
		const link = await dialog.getByLabel("Invitation link").inputValue();
		expect(link).toMatch(/^http:\/\/127\.0\.0\.1:5173\/invite#token=inv_/);
		await dialog.getByRole("button", { name: "Copy link for admin@vidyaclasses.in" }).click();
		expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(link);
		await expectNoAxeViolations(page, "accept link");
		await fullShot(page, screenshotPath(test.info(), "customers-invite-link"));
		await dialog.getByRole("button", { name: "Open customer" }).click();
		await expect(page.getByRole("heading", { level: 1, name: "Vidya Classes" })).toBeVisible();

		const owner = await secondPage(browser);
		await owner.goto(link.replace("http://127.0.0.1:5173", ""));
		await expect(owner.getByRole("heading", { name: "Join Vidya Classes" })).toBeVisible();
		await owner.getByLabel("Your name").fill("Sunita Kale");
		await owner.getByLabel("Password", { exact: true }).fill("Vidya classes 2026 ok");
		await owner.getByLabel("Repeat new password").fill("Vidya classes 2026 ok");
		await owner.getByRole("button", { name: "Accept and join" }).click();
		await expect(owner.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
		await owner.context().close();

		await page.getByRole("tab", { name: "Team" }).click();
		await expect(page.getByRole("rowheader", { name: /Sunita Kale/ })).toBeVisible();
		await checkPage(page, "customer-team");
	});

	test("customer detail tabs", async ({ page }) => {
		await logIn(page, "superadmin");
		await page.goto("/admin/customers?status=active");
		await rowOf(page, "Shree Labs").getByRole("link", { name: "Shree Labs" }).click();
		await expect(page.getByRole("heading", { level: 1, name: "Shree Labs" })).toBeVisible();
		await expect(page.getByTestId("plan-usage")).toContainText("Knowledge bases2 · unlimited");
		await checkPage(page, "customer-overview");
		await page.getByRole("tab", { name: "Knowledge bases" }).click();
		await expect(page.getByRole("link", { name: "Patient help" })).toBeVisible();
		// A8: the operator resends an open invitation and gets the new link.
		await page.getByRole("tab", { name: "Team" }).click();
		await page.getByRole("button", { name: "Resend invitation for asha@shree-labs.in" }).click();
		const resent = page.getByRole("dialog", { name: "Invitation for asha@shree-labs.in" });
		await expect(resent.getByLabel("Invitation link")).toHaveValue(/\/invite#token=inv_/);
		expect(world.accounts.sent.at(-1)).toMatchObject({ method: "POST", path: expect.stringMatching(/\/resend$/) });
		await expectNoAxeViolations(page, "invitation resent");
		await resent.getByRole("button", { name: "Done" }).click();
		// The customer's named keys: its migrated "Default key" and an automation's write key.
		await page.getByRole("tab", { name: "Keys" }).click();
		await expect(page.getByRole("rowheader", { name: /Default key/ })).toBeVisible();
		await page.getByRole("button", { name: "Rotate n8n automation" }).click();
		await page
			.getByRole("alertdialog", { name: "Rotate n8n automation?" })
			.getByRole("button", { name: "Rotate key" })
			.click();
		const reveal = page.getByRole("dialog", { name: "New key: n8n automation" });
		await expect(reveal.getByText(/^tk_/)).toBeVisible();
		await reveal.getByRole("button", { name: "Done" }).click();
		await page.getByRole("tab", { name: "Settings" }).click();
		await page.getByLabel("Custom limit").check();
		await page.getByLabel("Knowledge bases allowed").fill("5");
		await page.getByRole("button", { name: "Save" }).click();
		await expect(page.getByText("Customer saved")).toBeVisible();
		expect(world.accounts.sent.at(-1)).toMatchObject({ method: "PUT", body: { max_corpora: 5 } });
		await checkPage(page, "customer-settings");
		await page.getByRole("tab", { name: "Danger zone" }).click();
		await expect(page.getByRole("button", { name: "Suspend" })).toBeVisible();
		await checkPage(page, "customer-danger");
	});
});

test.describe("plans (super admin)", () => {
	test.beforeEach(({ browserName: _browser }, info) => {
		test.skip(roleOf(info) !== "superadmin", "super admin pages");
	});

	test("create Pro, make it the default, an in-use plan can't be deleted", async ({ page }) => {
		await logIn(page, "superadmin");
		await page.goto("/admin/plans");
		await expect(page.getByRole("heading", { level: 1, name: "Plans" })).toBeVisible();
		await expect(page.getByRole("article", { name: /^Starter/ })).toContainText("Default for new sign-ups");
		await expect(page.getByRole("article", { name: /^Standard/ })).toContainText("Built in");
		await page.getByRole("button", { name: "New plan" }).click();
		const dialog = page.getByRole("dialog", { name: "New plan" });
		await dialog.getByLabel("Name").fill("Pro");
		await dialog.getByLabel("Price note").fill("₹4,999 / month");
		await dialog.getByLabel("Knowledge bases").fill("3");
		await dialog.getByLabel("Questions a month").fill("10,000");
		await dialog.getByLabel("Documents").fill("2000");
		await dialog.getByLabel("Website widget keys").fill("5");
		await expectNoAxeViolations(page, "plan dialog");
		await fullShot(page, screenshotPath(test.info(), "plans-new"));
		await dialog.getByRole("button", { name: "Create plan" }).click();
		await expect(dialog).toBeHidden();
		const pro = page.getByRole("article", { name: /^Pro/ });
		await expect(pro).toContainText("10,000");
		expect(world.accounts.sent.at(-1)).toMatchObject({
			method: "POST",
			body: {
				name: "Pro",
				max_knowledge_bases: 3,
				monthly_questions: 10000,
				max_documents: 2000,
				max_widget_keys: 5,
				is_default: false,
			},
		});

		await page.getByRole("button", { name: "More actions for Pro" }).click();
		await page.getByRole("menuitem", { name: "Make default" }).click();
		await expect(page.getByText("Pro is now the default plan")).toBeVisible();
		await expect(page.getByTestId("plan-card").first()).toContainText("Default for new sign-ups");
		await expect(page.getByTestId("plan-card").first()).toContainText("Pro");

		await page.getByRole("button", { name: "More actions for Standard" }).click();
		await page.getByRole("menuitem", { name: "Delete" }).click();
		const del = page.getByRole("alertdialog", { name: "Delete Standard?" });
		await del.getByRole("button", { name: "Delete plan" }).click();
		await expect(del.getByText(/customers use this plan; move them first/)).toBeVisible();
		await expectNoAxeViolations(page, "plan in use");
		await del.getByRole("button", { name: "Cancel" }).click();
		expect(world.accounts.plans.some((p) => p.name === "Standard")).toBe(true);
		await checkPage(page, "plans");
	});
});

test("tenant accounts can't open Customers or Plans", async ({ page }, info) => {
	test.skip(roleOf(info) === "superadmin", "tenant view");
	await logIn(page, roleOf(info));
	for (const path of ["/admin/customers", "/admin/plans"]) {
		await page.goto(path);
		await expect(page.getByRole("heading", { name: "Not available for your account" })).toBeVisible();
	}
});

/**
 * Against the running stack: needs A3–A6 merged, the stack rebuilt, RAG_SUPERADMIN_EMAIL set and
 * the dev e-mail log provider (`docker compose logs mailer`) for the owner's "You're in" mail.
 */
test.describe("customers and plans on the real engine", () => {
	test.fixme("approval sign-up → activate with plan and note, suspend/reactivate/delete with typed names, add customer + accept_url, plans create/default/in-use (needs the running stack)", async () => {});
});
