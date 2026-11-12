import { type Browser, expect, type Page, test } from "@playwright/test";
import { ACCOUNTS, createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, type Role, roleOf, screenshotPath } from "./utils/session";

/**
 * D7 Profile & sessions (everyone) and Settings → Workspace key (tenants) on the mock engine: name,
 * two sessions → "Sign out everywhere else" leaves one, a password change signs the other browser
 * out, the key-principal view, create-or-rotate the workspace key. The same journeys against the
 * running stack are pending at the bottom of the file.
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

async function otherBrowser(browser: Browser, role: Role): Promise<Page> {
	const context = await browser.newContext({
		userAgent:
			"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
	});
	await installMockEngine(context, world);
	const page = await context.newPage();
	await logIn(page, role);
	return page;
}

test.beforeEach(async ({ context }) => {
	world = createWorld();
	await installMockEngine(context, world);
});

test("name, two sessions → sign out everywhere else → one; password change signs the other browser out", async ({
	page,
	browser,
}, info) => {
	test.setTimeout(120_000);
	const role = roleOf(info);
	await logIn(page, role);
	const other = await otherBrowser(browser, role);

	await page.goto("/profile");
	await expect(page.getByRole("heading", { level: 1, name: "Your profile" })).toBeVisible();
	const sessions = page.getByRole("list", { name: "Signed-in sessions" });
	await expect(sessions.getByTestId("session")).toHaveCount(2);
	await expect(sessions.getByTestId("session").first()).toContainText("this device");
	await expect(sessions).toContainText("Safari on iPhone");
	await checkPage(page, "profile");

	const name = page.getByLabel("Name");
	const before = await name.inputValue();
	await name.fill(`${before} K`);
	await page.getByRole("button", { name: "Save" }).click();
	await expect(page.getByText("Profile saved")).toBeVisible();
	expect(world.users.find((u) => u.email === ACCOUNTS[role].email)?.name).toBe(`${before} K`);

	await page.getByRole("button", { name: "Sign out everywhere else" }).click();
	await expect(page.getByText("Only this device is still signed in.")).toBeVisible();
	await expect(sessions.getByTestId("session")).toHaveCount(1);
	await other.goto("/usage");
	await expect(other.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();

	// Sign the other browser in again, then change the password here: it is signed out.
	await logIn(other, role);
	await page.getByLabel("Current password").fill(ACCOUNTS[role].password);
	await page.getByLabel("New password", { exact: true }).fill("Pune profile 2026 new");
	await page.getByLabel("Repeat new password").fill("Pune profile 2026 new");
	await page.getByRole("button", { name: "Change password" }).click();
	await expect(page.getByText("Your other sessions were signed out.")).toBeVisible();
	await other.goto("/usage");
	await expect(other.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
	await other.context().close();
	await expect(page.getByRole("heading", { level: 1, name: "Your profile" })).toBeVisible();
});

test("wrong current password is shown on the field", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "one role is enough");
	await logIn(page, "owner");
	await page.goto("/profile");
	await page.getByLabel("Current password").fill("not the password");
	await page.getByLabel("New password", { exact: true }).fill("Pune profile 2026 new");
	await page.getByLabel("Repeat new password").fill("Pune profile 2026 new");
	await page.getByRole("button", { name: "Change password" }).click();
	await expect(page.getByText("That isn't your current password.")).toBeVisible();
	await expectNoAxeViolations(page, "password error");
});

test("an API-key sign-in sees what the key is instead of profile, password and sessions", async ({ page }, info) => {
	test.skip(roleOf(info) !== "superadmin", "admin key");
	await page.goto("/login/key");
	await page.getByLabel("API key", { exact: true }).fill(ACCOUNTS.adminKey);
	await page.getByRole("button", { name: "Sign in", exact: true }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
	await page.goto("/profile");
	await expect(page.getByRole("heading", { name: "You're signed in with an API key" })).toBeVisible();
	await expect(page.getByTestId("key-principal")).toContainText("RAG_ADMIN_API_KEY");
	await expect(page.getByLabel("Current password")).toHaveCount(0);
	await checkPage(page, "profile-key");
});

test("Settings → Workspace: the workspace's named keys (owner), locked for members", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role === "superadmin", "tenant view");
	await logIn(page, role);
	await page.goto("/settings");
	const workspace = page.getByRole("region", { name: "Workspace", exact: true });
	await expect(workspace).toContainText("Shree Labs");
	const keyCard = page.getByRole("region", { name: "API keys" });
	if (role === "member") {
		await expect(keyCard.getByRole("button", { name: "Create key" })).toHaveAttribute("aria-disabled", "true");
		await expect(keyCard.getByText(/You're a Member/)).toBeVisible();
		await checkPage(page, "settings-workspace-member");
		return;
	}
	await expect(keyCard.getByRole("rowheader", { name: /Default key/ })).toBeVisible();
	await keyCard.getByRole("button", { name: "Create key" }).click();
	const dialog = page.getByRole("dialog", { name: "Create a key" });
	await dialog.getByLabel("Name").fill("Agent – Cursor");
	await dialog.getByRole("button", { name: "Create key" }).click();
	const reveal = page.getByRole("dialog", { name: "New key: Agent – Cursor" });
	const key = (await reveal.locator("code").textContent()) ?? "";
	expect(key).toMatch(/^tk_.*_mockNewSecret\d+$/);
	expect(world.apiKeys.sent.at(-1)?.body).toEqual({ name: "Agent – Cursor", permission: "read" });
	await expectNoAxeViolations(page, "key reveal");
	await fullShot(page, screenshotPath(info, "settings-workspace-key"));
	await reveal.getByRole("button", { name: "Done" }).click();
	await checkPage(page, "settings-workspace");
});

test("signed in with the workspace's tk_ key: the row is marked and rotating it signs this browser out", async ({
	page,
}, info) => {
	test.skip(roleOf(info) !== "owner", "tenant key");
	await page.goto("/login/key");
	await page.getByLabel("API key", { exact: true }).fill(ACCOUNTS.tenantKey);
	await page.getByRole("button", { name: "Sign in", exact: true }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	await page.goto("/settings");
	const keys = page.getByRole("region", { name: "API keys" });
	await expect(keys.getByText("In use here")).toBeVisible();
	await keys.getByRole("button", { name: "Rotate Default key" }).click();
	const confirm = page.getByRole("alertdialog", { name: "Rotate Default key?" });
	await expect(confirm).toContainText("You're signed in with this key");
	await confirm.getByRole("button", { name: "Rotate key" }).click();
	const reveal = page.getByRole("dialog", { name: "New key: Default key" });
	await reveal.getByRole("button", { name: "Done, sign me out" }).click();
	await expect(page.getByRole("heading", { name: /Log in to Sahayak AI|Sign in with an API key/ })).toBeVisible();
});

/** Against the running stack: needs A3 merged and the stack rebuilt. */
test.describe("profile on the real engine", () => {
	test.fixme("password change signs out another context; sessions list shows two, sign out everywhere else leaves one (needs the running stack)", async () => {});
});
