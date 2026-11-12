import { expect, type Page, test } from "@playwright/test";
import { ACCOUNTS, createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { logIn, openAdvanced, screenshotPath } from "./utils/session";

/**
 * The D1 auth journeys against the mock engine (runnable before A3–A5 merge). auth.spec.ts
 * repeats them against the real stack. Codes: sign-up 482913, reset 654321 (mock only).
 */

let world: World;

async function useWorld(page: Page, w: World = createWorld()) {
	world = w;
	await installMockEngine(page.context(), world);
}

test("wrong password → message, password cleared", async ({ page }) => {
	await useWorld(page);
	await page.goto("/login");
	await page.getByLabel("Email").fill(ACCOUNTS.owner.email);
	await page.getByLabel("Password", { exact: true }).fill("not the password");
	await page.getByRole("button", { name: "Log in" }).click();
	await expect(page.getByRole("alert")).toContainText("Email or password isn't right.");
	await expect(page.getByLabel("Password", { exact: true })).toHaveValue("");
	await expectNoAxeViolations(page, "login error");
});

test("super admin, owner and member each land on their own view", async ({ page }) => {
	await useWorld(page);
	await logIn(page, "superadmin");
	const nav = page.getByRole("navigation", { name: "Main" });
	await expect(nav.getByRole("link", { name: "Customers" })).toBeVisible();
	await page.getByRole("button", { name: /^Account:/ }).click();
	await expect(page.getByRole("menu")).toContainText("Super admin");
	await page.getByRole("menuitem", { name: "Log out" }).click();
	await expect(page.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
	for (const role of ["owner", "member"] as const) {
		await logIn(page, role);
		await expect(nav.getByRole("link", { name: "Team" })).toBeVisible();
		await expect(nav.getByRole("link", { name: "Customers" })).toHaveCount(0);
		await page.getByRole("button", { name: /^Account:/ }).click();
		await expect(page.getByRole("menu")).toContainText(role === "owner" ? "Owner · Shree Labs" : "Member · Shree Labs");
		await page.getByRole("menuitem", { name: "Log out" }).click();
		await expect(page.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
	}
});

test("sign-up in Approval mode → verify → pending → activated → Check again → Overview", async ({ page }, info) => {
	await useWorld(page, createWorld({ signupMode: "approval" }));
	await page.goto("/login");
	await page.getByRole("link", { name: "Create an account" }).click();
	await expect(page.getByRole("heading", { name: "Create your workspace" })).toBeVisible();
	await expectNoAxeViolations(page, "signup");
	await page.getByLabel("Business name").fill("Anand Dental Care");
	await page.getByLabel("Your name").fill("Anand Joshi");
	await page.getByLabel("Work email").fill("Dr.Anand@AnandDental.in");
	await page.getByLabel("Password", { exact: true }).fill("Kothrud clinic 2026");
	await page.getByRole("button", { name: "Create account" }).click();
	await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
	await expect(page).toHaveURL(/\/verify$/);
	await expect(page.getByText("dr.anand@ananddental.in")).toBeVisible();
	await expectNoAxeViolations(page, "verify");
	await page.screenshot({ path: screenshotPath(info, "verify") });
	await page.getByLabel("Digit 1 of 6").click();
	await page.keyboard.type("482913");
	await expect(page.getByRole("heading", { name: "We'll activate your workspace soon" })).toBeVisible();
	await expect(page.getByRole("main").getByText("support@sahayak.ai")).toBeVisible();
	await expectNoAxeViolations(page, "pending");
	await page.screenshot({ path: screenshotPath(info, "pending") });
	await page.getByRole("button", { name: "Check again" }).click();
	await expect(page.getByText("Still waiting")).toBeVisible();
	world.activate("Anand Dental Care");
	await page.getByRole("button", { name: "Check again" }).click();
	await expect(page.getByRole("heading", { name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
});

test("forgot → reset → log in with the new password", async ({ page }) => {
	await useWorld(page);
	await page.goto("/login");
	await page.getByLabel("Email").fill(ACCOUNTS.owner.email);
	await page.getByRole("link", { name: "Forgot password?" }).click();
	await expect(page.getByRole("heading", { name: "Reset your password" })).toBeVisible();
	await expect(page.getByLabel("Email")).toHaveValue(ACCOUNTS.owner.email);
	await page.getByRole("button", { name: "Send code" }).click();
	await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
	await expectNoAxeViolations(page, "reset");
	await page.getByLabel("Digit 1 of 6").click();
	await page.keyboard.type("654321");
	await page.getByLabel("New password", { exact: true }).fill("Baner road 2026 new");
	await page.getByLabel("Repeat new password").fill("Baner road 2026 new");
	await page.getByRole("button", { name: "Save new password" }).click();
	await expect(page.getByText("Password changed. Your other devices were signed out.")).toBeVisible();
	await expect(page.getByLabel("Email")).toHaveValue(ACCOUNTS.owner.email);
	await page.getByLabel("Password", { exact: true }).fill("Baner road 2026 new");
	await page.getByRole("button", { name: "Log in" }).click();
	await expect(page.getByRole("heading", { name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
});

test("set-password link: the fragment is removed from the address bar, then the password works", async ({ page }) => {
	await useWorld(page);
	await page.goto("/set-password#email=sv%40punedigital.in&code=654321");
	await expect(page.getByRole("heading", { name: "Set your admin password" })).toBeVisible();
	expect(new URL(page.url()).hash).toBe("");
	await page.getByLabel("New password", { exact: true }).fill("Swargate admin 2026");
	await page.getByLabel("Repeat new password").fill("Swargate admin 2026");
	await page.getByRole("button", { name: "Set password" }).click();
	await expect(page.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
	await page.getByLabel("Password", { exact: true }).fill("Swargate admin 2026");
	await page.getByRole("button", { name: "Log in" }).click();
	await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Customers" })).toBeVisible();
});

test("invitation link → new account → tenant view", async ({ page }, info) => {
	await useWorld(page);
	await page.goto("/invite#token=inv_mock_new_member");
	await expect(page.getByRole("heading", { name: "Join Shree Labs" })).toBeVisible();
	expect(new URL(page.url()).hash).toBe("");
	await expectNoAxeViolations(page, "invite");
	await page.screenshot({ path: screenshotPath(info, "invite") });
	await page.getByLabel("Your name").fill("Asha Pawar");
	await page.getByLabel("Password", { exact: true }).fill("Front desk 2026 ok");
	await page.getByLabel("Repeat new password").fill("Front desk 2026 ok");
	await page.getByRole("button", { name: "Accept and join" }).click();
	await expect(page.getByRole("heading", { name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Team" })).toBeVisible();
});

test("closed mode → Talk to us", async ({ page }, info) => {
	await useWorld(page, createWorld({ signupMode: "closed" }));
	await page.goto("/signup");
	await expect(page.getByRole("heading", { name: "Talk to us to get started" })).toBeVisible();
	await expect(page.getByRole("link", { name: "Contact us" })).toHaveAttribute("href", "https://wa.me/919822012345");
	await expectNoAxeViolations(page, "closed");
	await page.screenshot({ path: screenshotPath(info, "closed") });
});

test("API key sign-in: the admin key opens the admin view; a ck_ key is refused", async ({ page }, info) => {
	await useWorld(page);
	await page.goto("/login/key");
	await page.getByLabel("API key").fill(ACCOUNTS.corpusKey);
	await page.getByRole("button", { name: "Sign in" }).click();
	await expect(page.getByRole("alert")).toContainText("Knowledge-base keys don't open the dashboard");
	await expectNoAxeViolations(page, "key refused");
	await page.screenshot({ path: screenshotPath(info, "key-refused") });
	expect(await page.evaluate(() => sessionStorage.getItem("cob.key"))).toBeNull();
	await page.getByLabel("API key").fill(ACCOUNTS.adminKey);
	await page.getByRole("button", { name: "Sign in" }).click();
	await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
	await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Customers" })).toBeVisible();
});

test("access-token expiry (page.clock past 15 min): the next call refreshes transparently", async ({ page }) => {
	await useWorld(page);
	await page.clock.install();
	await logIn(page, "owner");
	const before = world.stats.refresh;
	world.expireAccessTokens();
	await page.clock.fastForward("16:00");
	await expect.poll(() => world.stats.refresh).toBeGreaterThan(before);
	await page.keyboard.press("Control+k");
	const palette = page.getByRole("dialog", { name: "Command palette" });
	await expect(palette.getByText("Patient help")).toBeVisible();
	await page.keyboard.press("Escape");
	await openAdvanced(page);
	await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Usage", exact: true }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Usage" })).toBeVisible();
	expect(world.stats.reuse).toBe(0);
});

test('two tabs with "Keep me signed in" refreshing at once both stay signed in', async ({ page, context }) => {
	await useWorld(page);
	await page.goto("/login");
	await page.getByLabel("Email").fill(ACCOUNTS.owner.email);
	await page.getByLabel("Password", { exact: true }).fill(ACCOUNTS.owner.password);
	await page.getByRole("checkbox", { name: /Keep me signed in/ }).check();
	await expect(page.getByText("Only on a device you trust")).toBeVisible();
	await page.getByRole("button", { name: "Log in" }).click();
	await expect(page.getByRole("heading", { name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	const second = await context.newPage();
	await second.goto("/usage");
	await expect(second.getByRole("heading", { level: 1, name: "Usage" })).toBeVisible();
	for (let round = 0; round < 3; round++) {
		world.expireAccessTokens();
		await Promise.all([page.reload(), second.reload()]);
		await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
		await expect(second.getByRole("heading", { level: 1, name: "Usage" })).toBeVisible();
	}
	expect(world.stats.reuse).toBe(0);
	expect(world.stats.refresh).toBeGreaterThanOrEqual(7);
});

test("logging out in one kept tab signs the other out too", async ({ page, context }) => {
	await useWorld(page);
	await page.goto("/login");
	await page.getByLabel("Email").fill(ACCOUNTS.owner.email);
	await page.getByLabel("Password", { exact: true }).fill(ACCOUNTS.owner.password);
	await page.getByRole("checkbox", { name: /Keep me signed in/ }).check();
	await page.getByRole("button", { name: "Log in" }).click();
	await expect(page.getByRole("heading", { name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	const second = await context.newPage();
	await second.goto("/");
	await expect(second.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	await page.getByRole("button", { name: /^Account:/ }).click();
	await page.getByRole("menuitem", { name: "Log out" }).click();
	await expect(second.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
});
