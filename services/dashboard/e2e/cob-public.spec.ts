import { expect, type Page, test } from "@playwright/test";
import { publicChats } from "./fixtures/mock-cob-public";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone } from "./utils/session";

/**
 * COB6: "Ask COB" on the sign-in pages, signed out, on the mock engine
 * (e2e/fixtures/mock-cob-public.ts). A slide-over on desktop, full-screen on phones.
 */
const launcher = (page: Page) => page.getByTestId("cob-public-launcher");
const panel = (page: Page) => page.getByRole("dialog", { name: /COB, ask about the engine/ });

test("login page: Ask COB opens, answers from public pages and links only to public places", async ({ page }) => {
	const world = createWorld();
	await installMockEngine(page.context(), world);
	await page.goto("/login");
	await expect(launcher(page)).toBeVisible();
	await launcher(page).click();
	await expect(panel(page)).toBeVisible();
	await expect(page.getByRole("textbox", { name: "Ask COB" })).toBeFocused();
	await expectNoAxeViolations(page, "ask cob open");

	await panel(page).getByRole("button", { name: "What is cob-rag-engine?" }).click();
	const turn = panel(page).getByTestId("cob-turn");
	await expect(turn.getByText(/self-hosted engine/)).toBeVisible();
	await expect(turn.getByRole("link", { name: /What is cob-rag-engine\? \(new tab\)/ })).toHaveAttribute(
		"href",
		/callobuzz\.github\.io/,
	);
	await expect(turn.getByRole("link", { name: "Open Log in →" })).toBeVisible();
	await expect(panel(page).getByRole("button", { name: /Good answer|Bad answer/ })).toHaveCount(0);
	await expect(panel(page).getByText(/isn't saved/)).toBeVisible();
	await expectNoAxeViolations(page, "ask cob answer");

	// The next question carries this one as history; nothing is kept in the browser.
	await page.getByRole("textbox", { name: "Ask COB" }).fill("And can I sign up?");
	await page.keyboard.press("Enter");
	await expect(panel(page).getByTestId("cob-turn")).toHaveCount(2);
	await expect.poll(() => (publicChats.get(world) ?? []).length).toBe(2);
	const second = publicChats.get(world)?.[1] as { history?: { role: string }[] };
	expect(second.history?.map((h) => h.role)).toEqual(["user", "assistant"]);
	expect(await page.evaluate(() => Object.keys(localStorage).filter((k) => /cob/i.test(k)))).toEqual([]);

	await page.keyboard.press("Escape");
	await expect(panel(page)).toBeHidden();
	await expect(launcher(page)).toBeFocused();
});

test("a rate-limited visitor is told how long to wait", async ({ page }) => {
	await installMockEngine(page.context(), createWorld());
	await page.goto("/login");
	await launcher(page).click();
	await page.getByRole("textbox", { name: "Ask COB" }).fill("limit please");
	await page.keyboard.press("Enter");
	await expect(panel(page).getByRole("alert")).toContainText("10 minutes");
});

test("the other sign-in pages have it too, and a phone gets it full-screen", async ({ page }) => {
	await installMockEngine(page.context(), createWorld());
	for (const path of ["/signup", "/forgot"]) {
		await page.goto(path);
		await expect(launcher(page)).toBeVisible();
	}
	await launcher(page).click();
	const box = await page.getByTestId("cob-public-sheet").boundingBox();
	const width = page.viewportSize()?.width ?? 0;
	if (isPhone(page)) expect(Math.round(box?.width ?? 0)).toBe(width);
	else expect(box?.width ?? 0).toBeLessThanOrEqual(440);
});

test("switched off: no button on any sign-in page", async ({ page }) => {
	const world = createWorld();
	world.branding.cob_public = false;
	await installMockEngine(page.context(), world);
	await page.goto("/login");
	await expect(page.getByLabel("Email")).toBeVisible();
	await expect(launcher(page)).toHaveCount(0);
});
