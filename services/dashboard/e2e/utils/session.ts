import { expect, type Page, type TestInfo } from "@playwright/test";
import { ACCOUNTS } from "../fixtures/mock-engine";

export type Role = "superadmin" | "owner" | "member";

export function roleOf(info: TestInfo): Role {
	return (info.project.metadata?.role as Role | undefined) ?? "owner";
}

/** Email + password sign-in through the real login form. */
export async function logIn(page: Page, role: Role): Promise<void> {
	const acct = ACCOUNTS[role];
	await page.goto("/login");
	await page.getByLabel("Email").fill(acct.email);
	await page.getByLabel("Password", { exact: true }).fill(acct.password);
	await page.getByRole("button", { name: "Log in" }).click();
	// BIZ3: a business lands on Home, the operator on Overview
	await expect(page.getByRole("heading", { name: role === "superadmin" ? "Overview" : "Home" })).toBeVisible();
}

export function isPhone(page: Page): boolean {
	return (page.viewportSize()?.width ?? 1440) < 861;
}

/** Screenshots go to E2E_SCREENSHOTS (outside the repo) when set, else the test output folder. */
export function screenshotPath(info: TestInfo, name: string): string {
	const dir = process.env.E2E_SCREENSHOTS;
	const file = `${info.project.name}-${name}.png`;
	return dir ? `${dir.replace(/[\\/]+$/, "")}/${file}` : info.outputPath(file);
}

/**
 * A business's menu keeps everything technical under a folded "Advanced" group (BIZ3). Open it so a
 * page inside it (Usage, Documents, Content Portal ...) can be reached by its menu link. A no-op
 * for the operator, who has no such group, and when it is already open.
 */
export async function openAdvanced(page: Page): Promise<void> {
	const nav = page.getByRole("navigation", { name: "Main" });
	// The menu can be briefly absent while the shell re-renders (a token refresh); wait for it.
	await expect(nav.getByRole("link").first()).toBeVisible();
	const button = nav.getByRole("button", { name: "Advanced" });
	if ((await button.count()) > 0 && (await button.getAttribute("aria-expanded")) === "false") await button.click();
}
