import { expect, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, openAdvanced, roleOf, screenshotPath } from "./utils/session";

/**
 * Shell (D1): every placeholder route inside the shell with PoweredBy and zero axe violations,
 * the drawer at 390 px, the Ctrl+K palette, theme switching and a keyboard-only pass. Runs on the
 * mock engine across the README project matrix.
 */

const BOTH = [
	["/", "Overview"],
	["/kbs", "Knowledge bases"],
	["/kbs/7b221003-0000-4000-8000-000000000001", "Patient help"],
	["/documents", "Documents"],
	["/documents/d0c00000-0000-4000-8000-000000000999", "Document not found"],
	["/add", "Add content"],
	["/playground", "Playground"],
	["/conversations", "Conversations"],
	["/widget", "Website widget"],
	["/agents", "Agents (MCP)"],
	["/usage", "Usage"],
	["/profile", "Your profile"],
	["/crawler", "Crawler"],
	["/crawler/job-1", "Crawl job"],
	["/insights", "Insights"],
	["/quality", "Quality"],
	["/migrations", "Migrations"],
	["/models", "Models"],
	["/settings", "Settings"],
] as const;
const ADMIN_ONLY = [
	["/admin/customers", "Customers"],
	["/admin/customers/33333333-3333-4333-8333-333333333333", "Customer not found"],
	["/admin/plans", "Plans"],
	["/admin/branding", "Sign-up & branding"],
	["/admin/email", "Email (SMTP)"],
	["/admin/audit", "Audit log"],
	["/admin/webhooks", "Webhooks"],
	["/admin/engine", "Engine info"],
] as const;
const TENANT_ONLY = [
	["/team", "Team"],
	["/plan", "Plan & billing"],
] as const;

let world: World;

test.beforeEach(async ({ context }) => {
	world = createWorld();
	await installMockEngine(context, world);
});

test("every route renders inside the shell with PoweredBy and no axe violations", async ({ page }, info) => {
	test.setTimeout(240_000);
	const role = roleOf(info);
	await logIn(page, role);
	const admin = role === "superadmin";
	const allowed = [...BOTH, ...(admin ? ADMIN_ONLY : TENANT_ONLY)];
	const blocked = admin ? TENANT_ONLY : ADMIN_ONLY;
	for (const [path, heading] of allowed) {
		await page.goto(path);
		// BIZ3: "/" is Overview for the operator and Home for a business
		const expected = path === "/" && !admin ? "Home" : heading;
		await expect(page.getByRole("heading", { level: 1, name: expected, exact: true }), path).toBeVisible();
		await expect(page.getByTestId("powered-by"), path).toBeVisible();
		await expectNoAxeViolations(page, path);
	}
	for (const [path] of blocked) {
		await page.goto(path);
		await expect(page.getByRole("heading", { name: "Not available for your account" }), path).toBeVisible();
		await expect(page.getByTestId("powered-by")).toBeVisible();
	}
	await page.goto("/no-such-page");
	await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
	await expectNoAxeViolations(page, "not found");
	// e-mail link paths exist (A2–A5): /admin/customers?status=pending for the admin.
	if (admin) {
		await page.goto("/admin/customers?status=pending");
		await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
	}
});

test("screenshots of the shell and sign-in", async ({ page }, info) => {
	const role = roleOf(info);
	await page.goto("/login");
	await expect(page.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
	await expectNoAxeViolations(page, "login");
	await page.screenshot({ path: screenshotPath(info, "login"), fullPage: true });
	await logIn(page, role);
	await page.screenshot({ path: screenshotPath(info, "overview") });
	await page.goto("/settings");
	await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
	await page.screenshot({ path: screenshotPath(info, "settings"), fullPage: true });
});

test("phones get a drawer; desktops a collapsible sidebar", async ({ page }) => {
	await logIn(page, roleOf(test.info()));
	if (isPhone(page)) {
		await expect(page.getByRole("navigation", { name: "Main" })).toBeHidden();
		await page.getByRole("button", { name: "Open menu" }).click();
		const nav = page.getByRole("navigation", { name: "Main" });
		await expect(nav).toBeVisible();
		await openAdvanced(page);
		await nav.getByRole("link", { name: "Usage", exact: true }).click();
		await expect(page.getByRole("heading", { level: 1, name: "Usage" })).toBeVisible();
		await expect(nav).toBeHidden();
		const width = await page.evaluate(() => document.documentElement.scrollWidth);
		expect(width).toBeLessThanOrEqual(390);
	} else {
		await page.getByRole("button", { name: "Collapse sidebar" }).click();
		await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
		await page.reload();
		await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
		await page.getByRole("button", { name: "Expand sidebar" }).click();
	}
});

test("Ctrl+K palette opens, filters and navigates", async ({ page }) => {
	await logIn(page, roleOf(test.info()));
	await page.keyboard.press("Control+k");
	const palette = page.getByRole("dialog", { name: "Command palette" });
	await expect(palette).toBeVisible();
	await expectNoAxeViolations(page, "palette");
	await palette.getByRole("combobox").fill("quality");
	await page.keyboard.press("Enter");
	await expect(page.getByRole("heading", { level: 1, name: "Quality" })).toBeVisible();
	await expect(palette).toBeHidden();
	await page.keyboard.press("Control+k");
	await expect(palette).toBeVisible();
	await page.keyboard.press("Escape");
	await expect(palette).toBeHidden();
});

test("theme switch light / dark persists across reloads", async ({ page }) => {
	await logIn(page, roleOf(test.info()));
	await page.getByRole("button", { name: /^Theme:/ }).click();
	await page.getByRole("menuitemradio", { name: "Dark" }).click();
	await expect(page.locator("html")).toHaveClass(/dark/);
	await page.reload();
	await expect(page.locator("html")).toHaveClass(/dark/);
	await page.getByRole("button", { name: /^Theme:/ }).click();
	await page.getByRole("menuitemradio", { name: "Light" }).click();
	await expect(page.locator("html")).not.toHaveClass(/dark/);
	await expect(page.getByRole("menu")).toBeHidden();
	await expectNoAxeViolations(page, "light theme");
});

test("keyboard only: log in, skip link, sidebar", async ({ page }, info) => {
	const role = roleOf(info);
	const { ACCOUNTS } = await import("./fixtures/mock-engine");
	await page.goto("/login");
	await expect(page.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
	await page.keyboard.press("Tab");
	await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
	// Tab until the e-mail field has focus (brand side has no focusable controls).
	for (let i = 0; i < 6; i++) {
		if (await page.getByLabel("Email").evaluate((el) => el === document.activeElement)) break;
		await page.keyboard.press("Tab");
	}
	await expect(page.getByLabel("Email")).toBeFocused();
	await page.keyboard.type(ACCOUNTS[role].email);
	await page.keyboard.press("Tab"); // forgot link
	await page.keyboard.press("Tab"); // password
	await expect(page.getByLabel("Password", { exact: true })).toBeFocused();
	await page.keyboard.type(ACCOUNTS[role].password);
	await page.keyboard.press("Enter");
	// After a client-side navigation focus moves to the page heading.
	// BIZ3: a business lands on Home, the operator on Overview
	await expect(
		page.getByRole("heading", { level: 1, name: role === "superadmin" ? "Overview" : "Home" }),
	).toBeFocused();
	// On a fresh load the skip link is the first stop.
	await page.reload();
	await expect(
		page.getByRole("heading", { level: 1, name: role === "superadmin" ? "Overview" : "Home" }),
	).toBeVisible();
	await page.keyboard.press("Tab");
	await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
	if (!isPhone(page)) {
		// Walk the sidebar to "Usage" and open it with Enter.
		for (let i = 0; i < 20; i++) {
			await page.keyboard.press("Tab");
			const name = await page.evaluate(() => document.activeElement?.textContent?.trim() ?? "");
			if (name.startsWith("Usage")) break;
			// BIZ3: a business's technical pages sit behind the folded Advanced group; open it by keyboard
			if (
				name === "Advanced" &&
				(await page.evaluate(() => document.activeElement?.getAttribute("aria-expanded"))) === "false"
			) {
				await page.keyboard.press("Enter");
			}
		}
		await page.keyboard.press("Enter");
		await expect(page.getByRole("heading", { level: 1, name: "Usage" })).toBeVisible();
		await expect(page.getByRole("heading", { level: 1, name: "Usage" })).toBeFocused();
	}
});
