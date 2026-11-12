import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, roleOf } from "./utils/session";

/**
 * COB8: the in-app Docs on the mock engine (e2e/fixtures/mock-docs.ts) across the README matrix:
 * opened from the sidebar, search, a page with contents and a copyable example with the engine
 * address, "Open this screen", the "?" on a screen, role-aware topics, and the phone layout.
 */
test.beforeEach(async ({ context }) => {
	await installMockEngine(context, createWorld());
});

async function openDocsFromSidebar(page: Page) {
	if (isPhone(page)) await page.getByRole("button", { name: "Open menu" }).click();
	// BIZ3: a business's menu calls the Docs "Help"
	const label = roleOf(test.info()) === "superadmin" ? "Docs" : "Help";
	await page.getByRole("navigation", { name: /main/i }).getByRole("link", { name: label, exact: true }).click();
	await expect(page).toHaveURL(/\/docs$/);
}

test("opens from the sidebar with a home, topics and the version", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await openDocsFromSidebar(page);
	await expect(page.getByRole("heading", { level: 1, name: "Docs" })).toBeVisible();
	await expect(page.getByText("Docs for engine 0.1.0")).toBeVisible();
	await expect(page.getByRole("heading", { name: "What's new" })).toBeVisible();
	await expectNoAxeViolations(page, "docs home");
});

test("search finds a page and opens it", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await page.goto("/docs");
	await page.getByRole("searchbox", { name: "Search the Docs" }).fill("widget");
	await expect(page.getByRole("heading", { name: "Search results for “widget”" })).toBeVisible();
	await page.getByRole("link", { name: /Website widget — Embed the widget/ }).click();
	await expect(page).toHaveURL(/\/docs\/screens\/widget#embed-the-widget$/);
	await expect(page.getByRole("heading", { level: 2, name: "Website widget" })).toBeVisible();
	await expect(page.getByRole("heading", { level: 2, name: "Embed the widget" })).toBeFocused();
});

test("a page has contents and a copyable example with this engine's address", async ({ page, context }, info) => {
	await context.grantPermissions(["clipboard-read", "clipboard-write"]);
	await logIn(page, roleOf(info));
	await page.goto("/docs/api/assistant");
	await expect(page.getByRole("heading", { level: 2, name: "Assistant API" })).toBeVisible();
	const code = page.locator("pre code").first();
	await expect(code).toContainText("curl http://engine.mock/api/v1/assistant");
	await expect(code).not.toContainText("{{BASE_URL}}");
	await expect(code).toContainText("$COB_RAG_KEY");
	await page.getByRole("button", { name: "Copy code" }).first().click();
	await expect(page.getByRole("button", { name: "Copied" })).toBeVisible();
	// Wide screens show the contents beside the page, narrow ones as a disclosure above it.
	if (isPhone(page)) await expect(page.locator("summary", { hasText: "On this page" })).toBeVisible();
	else await expect(page.getByRole("navigation", { name: "On this page" })).toBeVisible();
	await expectNoAxeViolations(page, "docs page");
});

test("Open this screen leads to the screen and the ? leads back", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await page.goto("/docs/screens/widget");
	await page.getByRole("link", { name: /Open this screen/ }).click();
	await expect(page).toHaveURL(/\/widget/);
	await page.getByRole("link", { name: "Help for this screen" }).click();
	await expect(page).toHaveURL(/\/docs\/screens\/widget$/);
	await expect(page.getByRole("heading", { level: 2, name: "Website widget" })).toBeVisible();
});

test("the settings reference is for the operator only", async ({ page }, info) => {
	const role = roleOf(info);
	await logIn(page, role);
	await page.goto("/docs");
	if (isPhone(page)) await page.getByRole("button", { name: "Browse the Docs" }).click();
	const nav = page.getByRole("navigation", { name: "Docs" });
	if (role === "superadmin") await expect(nav.getByText("Settings reference")).toBeVisible();
	else await expect(nav.getByText("Settings reference")).toHaveCount(0);

	await page.keyboard.press("Escape");
	await page.goto("/docs/settings/llm-tuning");
	if (role === "superadmin") {
		await expect(page.getByRole("heading", { level: 2, name: "LLM tuning settings" })).toBeVisible();
	} else {
		await expect(page.getByTestId("docs-not-found")).toBeVisible();
	}
});

test("fits the screen: no sideways scroll, navigation reachable", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await page.goto("/docs/api/assistant");
	await expect(page.getByRole("heading", { level: 2, name: "Assistant API" })).toBeVisible();
	const overflow = await page.evaluate(() => {
		const el = document.getElementById("content");
		return el ? el.scrollWidth - el.clientWidth : 0;
	});
	expect(overflow).toBeLessThanOrEqual(0);
	if (isPhone(page)) {
		await page.getByRole("button", { name: "Browse the Docs" }).click();
		const sheet = page.getByRole("dialog");
		await sheet.getByText("Website widget", { exact: true }).first().click(); // open the topic
		await sheet.getByRole("link", { name: "Website widget" }).click();
		await expect(page).toHaveURL(/\/docs\/screens\/widget/);
	}
});
