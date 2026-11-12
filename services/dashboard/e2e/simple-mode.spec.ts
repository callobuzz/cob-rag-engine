import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { ALL_SHOWN } from "./fixtures/mock-home";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * BIZ3: the business dashboard on the mock engine (e2e/fixtures/mock-home.ts), across the README
 * matrix: the plain menu with a folded Advanced group, the plan's flags in the menu and on deep
 * links, and the operator's menu unchanged. Indian demo data: Shree Dental Clinic, Pune.
 */
let world: World;
const PATIENT = "7b221003-0000-4000-8000-000000000001";

async function menu(page: Page) {
	if (isPhone(page) && !(await page.getByRole("navigation", { name: "Main" }).isVisible())) {
		await page.getByRole("button", { name: "Open menu" }).click();
	}
	return page.getByRole("navigation", { name: "Main" });
}

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth), name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

test.beforeEach(async ({ context }) => {
	world = createWorld();
	await installMockEngine(context, world);
});

test("a business lands on Home with the plain menu and a folded Advanced group", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role === "superadmin", "the operator's own menu is checked below");
	await logIn(page, role);
	await expect(page.getByTestId("assistant-status")).toContainText("Shree Dental Assistant is live");
	await expect(page.getByTestId("today-conversations")).toContainText("3");
	await checkPage(page, "home");

	const nav = await menu(page);
	const links = await nav.getByRole("link").allTextContents();
	expect(links.map((l) => l.replace(/\d+$/, "").trim())).toEqual([
		"Home",
		"Conversations & Leads",
		"Knowledge",
		"Assistant",
		"Plan & billing",
		"Team",
		"Help",
	]);
	const advanced = nav.getByRole("button", { name: "Advanced" });
	await expect(advanced).toHaveAttribute("aria-expanded", "false");
	await expect(nav.getByRole("link", { name: "Documents" })).toHaveCount(0);

	await advanced.click();
	await expect(advanced).toHaveAttribute("aria-expanded", "true");
	for (const name of ["Documents", "Website widget", "Models"]) {
		await expect(nav.getByRole("link", { name: new RegExp(`^${name}`) }), name).toBeVisible();
	}
	await nav.getByRole("link", { name: "Documents" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Documents" })).toBeVisible();
	// the group stays open on a page inside it
	const again = await menu(page);
	await expect(again.getByRole("button", { name: "Advanced" })).toHaveAttribute("aria-expanded", "true");
});

test("Knowledge and Assistant open from the menu", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role === "superadmin", "business pages");
	await logIn(page, role);
	await (await menu(page)).getByRole("link", { name: "Knowledge" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Knowledge" })).toBeVisible();
	await expect(page.getByRole("tab", { name: "From your website" })).toBeVisible();
	await page.getByRole("tab", { name: "Articles & files" }).click();
	await expect(page.getByRole("button", { name: "Write an article" })).toBeVisible();
	await checkPage(page, "knowledge");

	await (await menu(page)).getByRole("link", { name: "Assistant" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Assistant" })).toBeVisible();
	await expect(page.getByRole("heading", { name: "Name and business details" })).toBeVisible();
	await expect(page.getByRole("link", { name: "All settings" })).toBeVisible();
	await checkPage(page, "assistant");
});

test("a plan without Advanced shows no Advanced group", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role === "superadmin", "business menu");
	world.home.dashboard = { ...ALL_SHOWN, advanced: false };
	await logIn(page, role);
	const nav = await menu(page);
	await expect(nav.getByRole("link", { name: "Home" })).toBeVisible();
	await expect(nav.getByRole("button", { name: "Advanced" })).toHaveCount(0);
	await expect(nav.getByRole("link")).toHaveCount(7);
});

test("a deep link to a hidden knowledge base tab says it is not included", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role === "superadmin", "business menu");
	world.home.dashboard = { ...ALL_SHOWN, models: false };
	await logIn(page, role);
	await page.goto(`/kbs/${PATIENT}?tab=model`);
	const notice = page.getByTestId("not-in-plan");
	await expect(notice).toContainText("Not included in your plan");
	await expect(notice).toContainText("support@sahayak.ai");
	await expect(page.getByRole("tab", { name: "Model & keys" })).toHaveCount(0);
	await checkPage(page, "not-in-plan");
});

test("a code-only plan shows the website code and the allowed sites", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role === "superadmin", "business menu");
	world.home.dashboard = { ...ALL_SHOWN, widget_code_only: true };
	await logIn(page, role);
	await page.goto("/widget");
	const panel = page.getByTestId("widget-code-only");
	await expect(panel).toBeVisible();
	await expect(panel.getByTestId("snippet").first()).toContainText("widget.js");
	await expect(panel.getByRole("list", { name: "Allowed sites" }).first()).toBeVisible();
	await expect(page.getByText("Appearance")).toHaveCount(0);
	await checkPage(page, "widget-code-only");
});

test("the operator's menu is unchanged: no Advanced group, no business pages", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role !== "superadmin", "operator view");
	await logIn(page, role);
	const nav = await menu(page);
	await expect(nav.getByRole("link", { name: /^Customers/ })).toBeVisible();
	await expect(nav.getByRole("button", { name: "Advanced" })).toHaveCount(0);
	await expect(nav.getByRole("link", { name: "Knowledge", exact: true })).toHaveCount(0);
	if (isPhone(page)) await page.keyboard.press("Escape");
	await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
	await page.goto("/knowledge");
	await expect(page.getByRole("heading", { name: "Not available for your account" })).toBeVisible();
});
