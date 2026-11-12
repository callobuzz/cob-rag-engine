import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * P3 legal pages, on the mock engine (e2e/fixtures/mock-legal.ts): the provider fills in the details
 * the pages need, publishes Terms and Privacy, and then sign-up asks for the box and the footers
 * show the links. Indian demo data: Sahayak AI, Pune.
 */
let world: World;

test.beforeEach(async ({ context }) => {
	world = createWorld();
	await installMockEngine(context, world);
});

async function publish(page: Page, slug: string) {
	await page.getByTestId(`legal-edit-${slug}`).click();
	await expect(page.getByTestId("legal-review-banner")).toContainText("Have your lawyer review it");
	await page.getByTestId("legal-publish").click();
	const dialog = page.getByRole("alertdialog");
	await dialog.getByLabel("What changed (optional)").fill("First version");
	await dialog.getByLabel("I have had this page reviewed").click();
	await dialog.getByRole("button", { name: "Publish" }).click();
	await expect(dialog).toBeHidden();
	await expect(page.getByTestId("legal-history")).toContainText("Version 1");
	await page.getByRole("button", { name: "Back to all pages" }).click();
	await expect(page.getByTestId(`legal-status-${slug}`)).toContainText("Version 1");
}

test("the provider publishes Terms and Privacy; sign-up then asks for the box and the footer shows the links", async ({
	page,
	browser,
}, info) => {
	test.skip(roleOf(info) !== "superadmin", "the provider's page");
	await logIn(page, "superadmin");
	await page.goto("/admin/legal");
	await expect(page.getByRole("heading", { level: 1, name: "Legal pages" })).toBeVisible();
	await expect(page.getByTestId("legal-pages").getByText("Not published")).toHaveCount(8);

	// a page cannot go out while a detail it uses is missing
	await page.getByTestId("legal-edit-terms").click();
	await expect(page.getByTestId("legal-missing")).toContainText("the grievance officer's e-mail");
	await page.getByRole("button", { name: "Add them under Your details" }).click();
	await page.getByLabel("Grievance officer's name").fill("Asha Sharma");
	await page.getByLabel("Grievance officer's e-mail").fill("grievance@sahayak.example");
	await page.getByLabel("City whose courts the Terms name").fill("Pune");
	await page.getByRole("button", { name: "Save" }).click();
	await expect(page.getByText("Details saved")).toBeVisible();
	await page.getByRole("tab", { name: "Pages" }).click();

	await publish(page, "terms");
	await publish(page, "privacy");
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await expectNoAxeViolations(page, "admin-legal");
	await fullShot(page, screenshotPath(info, "admin-legal"));

	// a visitor signing up in another window
	const visitor = await browser.newContext({
		viewport: { width: 1440, height: 900 },
		baseURL: "http://127.0.0.1:5173",
	});
	await installMockEngine(visitor, world);
	const signup = await visitor.newPage();
	await signup.goto("/signup");
	await signup.getByLabel("Business name").fill("Shree Labs");
	await signup.getByLabel("Your name").fill("Priya Kulkarni");
	await signup.getByLabel("Work email").fill("priya@shree-labs.in");
	await signup.getByLabel("Password", { exact: true }).fill("a long pass phrase");
	const agreement = signup.getByTestId("signup-agreement");
	await expect(agreement).toContainText("I agree to the Terms and have read the Privacy notice");
	await expect(agreement.getByRole("link", { name: "Terms" })).toHaveAttribute("target", "_blank");
	await signup.getByRole("button", { name: "Create account" }).click();
	await expect(signup.getByText("Tick the box to agree before you sign up.")).toBeVisible();
	// the footer shows the two published pages
	const footer = signup.getByTestId("legal-links");
	await expect(footer.getByRole("link", { name: "Terms of Service" })).toBeVisible();
	await expect(footer.getByRole("link", { name: "Privacy Notice" })).toBeVisible();
	await expectNoAxeViolations(signup, "signup-legal");
	await visitor.close();
});
