import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, screenshotPath } from "./utils/session";

/**
 * White-label logo rule on the public pages, against the mock engine:
 *  - no logo_url: the cob-rag-engine chrome logo (small mark beside the name, the animated logo in the
 *    brand panel's centre on wide screens, a static mark above the form);
 *  - a logo_url: the operator's <img> in those places, no cob-rag-engine animation, a backdrop instead.
 * "cob-rag-engine by Callobuzz" stays either way.
 */

const LOGO_URL = "http://logo.mock/sahayak.svg";
const LOGO_SVG =
	'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0b7a5f"/><path d="M18 34l10 10 18-22" fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

async function withOperatorLogo(page: Page) {
	const world = createWorld();
	world.branding.logo_url = LOGO_URL;
	await installMockEngine(page.context(), world);
	await page.route(LOGO_URL, (route) => route.fulfill({ status: 200, contentType: "image/svg+xml", body: LOGO_SVG }));
}

test.describe("default brand", () => {
	test("login shows the cob-rag-engine logo, animated only on wide screens", async ({ page }, info) => {
		await installMockEngine(page.context(), createWorld());
		await page.goto("/login");
		await expect(page.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
		await expect(page.getByTestId("operator-logo")).toHaveCount(0);
		await expect(page.getByTestId("powered-by")).toContainText("cob-rag-engine by Callobuzz");
		const animated = page.getByTestId("cob-logo-animated");
		if (isPhone(page)) {
			await expect(animated).toBeHidden();
			// Only the small mark in the collapsed panel; none above the form.
			await expect(page.getByTestId("cob-logo-mark").filter({ visible: true })).toHaveCount(1);
		} else {
			await expect(animated).toBeVisible();
			await expect(page.getByTestId("cob-logo-mark").filter({ visible: true })).toHaveCount(2);
			// armed -> assemble -> live once it has been on screen.
			await expect(animated).toHaveAttribute("data-state", "live", { timeout: 8000 });
		}
		await page.screenshot({ path: screenshotPath(info, "login-default"), fullPage: true });
		await expectNoAxeViolations(page, "login default brand");
	});

	test("the animated logo holds under reduced motion", async ({ page }) => {
		await page.emulateMedia({ reducedMotion: "reduce" });
		await installMockEngine(page.context(), createWorld());
		await page.goto("/login");
		test.skip(isPhone(page), "no animated logo on phones");
		const animated = page.getByTestId("cob-logo-animated");
		await expect(animated).toBeVisible();
		await expect(animated).not.toHaveAttribute("data-state", /.+/);
	});

	test("the other public pages share the layout and logo", async ({ page }, info) => {
		await installMockEngine(page.context(), createWorld());
		for (const path of ["/signup", "/forgot", "/verify", "/reset", "/set-password", "/invite", "/status"]) {
			await page.goto(path);
			await expect(page.getByTestId("powered-by")).toBeVisible();
			await expect(page.getByTestId("cob-logo-mark").filter({ visible: true }).first()).toBeVisible();
			await expectNoAxeViolations(page, path);
		}
		await page.goto("/signup");
		await page.screenshot({ path: screenshotPath(info, "signup-default"), fullPage: true });
	});

	test("the signed-in sidebar uses the cob mark, not a letter badge", async ({ page }) => {
		await installMockEngine(page.context(), createWorld());
		await logIn(page, "owner");
		if (isPhone(page)) await page.getByRole("button", { name: /menu/i }).first().click();
		await expect(page.getByTestId("cob-logo-mark").filter({ visible: true }).first()).toBeVisible();
	});
});

test.describe("operator logo", () => {
	test("login shows the operator's logo and no cob-rag-engine animation", async ({ page }, info) => {
		await withOperatorLogo(page);
		await page.goto("/login");
		await expect(page.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
		await expect(page.getByTestId("cob-logo-animated")).toHaveCount(0);
		await expect(page.getByTestId("cob-logo-mark")).toHaveCount(0);
		await expect(page.getByTestId("powered-by")).toContainText("cob-rag-engine by Callobuzz");
		const logos = page.getByTestId("operator-logo").filter({ visible: true });
		if (isPhone(page)) {
			await expect(logos).toHaveCount(1);
			await expect(page.getByTestId("brand-backdrop")).toBeHidden();
		} else {
			await expect(logos).toHaveCount(2);
			await expect(page.getByRole("img", { name: "Sahayak AI" })).toHaveAttribute("src", LOGO_URL);
			await expect(page.getByTestId("brand-backdrop")).toBeVisible();
		}
		await page.screenshot({ path: screenshotPath(info, "login-operator"), fullPage: true });
		await expectNoAxeViolations(page, "login operator logo");
	});
});
