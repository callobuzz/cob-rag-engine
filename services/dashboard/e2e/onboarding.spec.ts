import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * BIZ4: a new business owner's first run on the mock engine (e2e/fixtures/mock-onboarding.ts):
 * the sign-in lands on the first-run steps, the owner types the website, copies the code, the
 * light turns green when the site has loaded the assistant, and the last step chats with it.
 * Indian demo data: Anand Dental Care, Pune.
 */
let world: World;
const BUSINESS = "Anand Dental Care";
const OWNER = { email: "meera@ananddental.in", password: "Anand dental owner 2026" };

test.beforeEach(async ({ context }) => {
	world = createWorld();
	world.signUp(BUSINESS, "Dr. Meera Anand", OWNER.email, OWNER.password);
	world.activate(BUSINESS);
	await installMockEngine(context, world);
});

async function signInAsNewOwner(page: import("@playwright/test").Page) {
	await page.goto("/login");
	await page.getByLabel("Email").fill(OWNER.email);
	await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
	await page.getByRole("button", { name: "Log in" }).click();
}

async function checkPage(page: import("@playwright/test").Page, name: string) {
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth), name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

test("a new owner lands on the first-run steps and finishes them", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the new business's own owner");
	await signInAsNewOwner(page);
	await expect(page.getByRole("heading", { level: 1, name: "Welcome to Sahayak AI" })).toBeVisible();
	await expect(page).toHaveURL(/\/welcome\?step=1/);

	// 1. the website
	await page.getByLabel("Your website").fill("ananddental.in");
	await page.getByLabel("What should your assistant be called?").fill("Anand Dental Assistant");
	await page.getByLabel("What kind of business is it?").selectOption("clinic");
	await page.getByLabel("WhatsApp number (optional)").fill("+91 98220 12345");
	await checkPage(page, "welcome-1");
	await page.getByRole("button", { name: "Continue" }).click();

	// TV5: 2. brochures and price lists (optional): upload a price list, wait until it is read
	await expect(page.getByRole("heading", { name: "Add your brochures, price lists and menus" })).toBeVisible();
	await expect(page).toHaveURL(/\/welcome\?step=2/);
	await page
		.getByTestId("file-input")
		.setInputFiles(fileURLToPath(new URL("./fixtures/files/test-price-list-oct.pdf", import.meta.url)));
	await page.getByRole("button", { name: "Upload 1 file" }).click();
	await expect(page.getByTestId("brochure-status")).toContainText("Your assistant has read all your files.", {
		timeout: 20_000,
	});
	await checkPage(page, "welcome-brochures");
	await page.getByRole("button", { name: "Next" }).click();
	// /TV5

	// 3. the code, and the light
	await expect(page.getByRole("heading", { name: "Add your assistant to your website" })).toBeVisible();
	await expect(page.getByTestId("install-snippet")).toContainText("widget.js");
	await expect(page.getByTestId("install-snippet")).toContainText('data-key="pk_');
	const light = page.getByTestId("connection-light");
	await expect(light).toHaveAttribute("data-state", "waiting");
	await page.getByRole("tab", { name: "Wix" }).click();
	await expect(page.getByText(/Custom code/)).toBeVisible();
	await page.getByRole("tab", { name: "Send to my developer" }).click();
	await expect(page.getByRole("link", { name: "Send on WhatsApp" })).toHaveAttribute(
		"href",
		/^https:\/\/wa\.me\/\?text=/,
	);
	await checkPage(page, "welcome-2");

	// the site loads the widget: the engine records it, the light turns green on its own
	const tenant = world.tenants.find((t) => t.name === BUSINESS);
	const row = world.onboarding.rows.get(tenant?.id ?? "");
	expect(row?.website).toBe("https://ananddental.in");
	if (row) row.connected = true;
	await expect(light).toHaveAttribute("data-state", "connected", { timeout: 15_000 });
	await expect(light).toContainText("Connected. Your assistant is live on ananddental.in.");
	await page.getByRole("button", { name: "Next" }).click();

	// 4. try it
	await expect(page.getByRole("heading", { name: "Try your assistant" })).toBeVisible();
	await expect(page.getByTestId("learning-progress")).toContainText(
		/Learning your website: \d+ pages read|Your website is learned/,
	);
	const chips = page.getByTestId("suggestions");
	await expect(chips.getByRole("button", { name: "What are your prices?" })).toBeVisible();
	await page.getByRole("radio", { name: "Hinglish" }).click();
	await expect(chips.getByRole("button", { name: "Aapki fees kitni hai?" })).toBeVisible();
	await page.getByRole("radio", { name: "English" }).click();
	await chips.getByRole("button", { name: "What are your prices?" }).click();
	const log = page.getByTestId("try-chat-log");
	await expect(log).toContainText("What are your prices?");
	await expect(log.locator("p")).toHaveCount(2);
	await expect(log.locator("p").nth(1)).not.toHaveText("…", { timeout: 15_000 });
	await checkPage(page, "welcome-3");

	// the end: Home, with the steps open again from a quiet link
	await page.getByRole("button", { name: "Go to my dashboard" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
	await expect(page.getByTestId("setup-reopen")).toBeVisible();
	expect(world.onboarding.patches).toContainEqual({ status: "done" });
});

// TV5
test("the brochure step can be skipped, and an owner who stopped on install resumes there", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the new business's own owner");
	await signInAsNewOwner(page);
	await page.getByLabel("Your website").fill("ananddental.in");
	await page.getByRole("button", { name: "Continue" }).click();
	await expect(page.getByRole("heading", { name: "Add your brochures, price lists and menus" })).toBeVisible();
	await page.getByRole("button", { name: "Skip this step" }).click();
	await expect(page.getByRole("heading", { name: "Add your assistant to your website" })).toBeVisible();
	await expect(page).toHaveURL(/\/welcome\?step=3/);
	await page.goto("/welcome");
	await expect(page.getByRole("heading", { name: "Add your assistant to your website" })).toBeVisible();
});
// /TV5

test("skipping the steps goes Home and does not bounce back; the steps stay one click away", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "the new business's own owner");
	await signInAsNewOwner(page);
	await expect(page.getByRole("heading", { level: 1, name: "Welcome to Sahayak AI" })).toBeVisible();
	await page.getByRole("button", { name: "Skip for now" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
	await expect(page.getByText("Set up your first assistant")).toBeVisible();
	await page.reload();
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
});

test("a member sees that the owner is setting things up", async ({ page }, info) => {
	test.skip(roleOf(info) !== "member", "a team member");
	await logIn(page, "member");
	await page.goto("/welcome");
	await expect(page.getByText("Your owner is setting things up")).toBeVisible();
	await expect(page.getByLabel("Your website")).toHaveCount(0);
	await checkPage(page, "welcome-member");
});
