import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, roleOf } from "./utils/session";

/**
 * COB3: the COB button and panel on every signed-in screen, on the mock engine
 * (e2e/fixtures/mock-cob.ts) across the README matrix. Desktop widths get the docked, non-modal
 * panel; the 390 px projects the full-screen sheet.
 */
test.beforeEach(async ({ context }) => {
	await installMockEngine(context, createWorld());
});

const launcher = (page: Page) => page.getByRole("button", { name: /^Ask COB/ });
const panel = (page: Page) =>
	isPhone(page)
		? page.getByRole("dialog", { name: "COB assistant" })
		: page.getByRole("complementary", { name: "COB assistant" });

async function signIn(page: Page, role: Parameters<typeof logIn>[1]) {
	await logIn(page, role);
	await expect(launcher(page)).toBeVisible();
}

test("opens with the button and with Ctrl+/; the starters belong to the screen", async ({ page }, info) => {
	await signIn(page, roleOf(info));
	await launcher(page).click();
	await expect(panel(page)).toBeVisible();
	// A modal sheet hides the page from the accessibility tree, so reach the button by test id.
	await expect(page.getByTestId("cob-launcher")).toHaveAttribute("aria-expanded", "true");
	await expect(page.getByRole("textbox", { name: "Ask COB" })).toBeFocused();
	await expect(panel(page).getByRole("button", { name: "What can I do on this screen?" })).toBeVisible();
	await expectNoAxeViolations(page, "cob panel open");

	await page.keyboard.press("Control+/");
	await expect(panel(page)).toBeHidden();
	await page.keyboard.press("Control+/");
	await expect(panel(page)).toBeVisible();
	await page.keyboard.press("Escape");
	await expect(panel(page)).toBeHidden();
	await expect(launcher(page)).toBeFocused();
});

test("a starter streams an answer with a source chip and a link; following it keeps the panel", async ({
	page,
}, info) => {
	await signIn(page, roleOf(info));
	await launcher(page).click();
	await panel(page).getByRole("button", { name: "What can I do on this screen?" }).click();
	const turn = panel(page).getByTestId("cob-turn");
	await expect(turn.locator("strong", { hasText: "Website widget" })).toBeVisible();
	await expect(turn.getByRole("link", { name: /Open Website widget: Embed the widget/ })).toBeVisible();
	await expect(turn.getByRole("button", { name: "Good answer" })).toBeVisible();
	await expectNoAxeViolations(page, "cob answer");

	await turn.getByRole("link", { name: "Open Website widget →" }).click();
	await expect(page).toHaveURL(/\/widget/);
	await expect(panel(page)).toBeVisible();
	await expect(panel(page).getByTestId("cob-turn")).toBeVisible();
});

test("history: reopen an earlier chat, then delete it", async ({ page }, info) => {
	await signIn(page, roleOf(info));
	await launcher(page).click();
	const box = page.getByRole("textbox", { name: "Ask COB" });
	await box.fill("How do I embed the widget?");
	await box.press("Enter");
	await expect(panel(page).getByRole("button", { name: "Good answer" })).toBeVisible();
	await panel(page).getByRole("button", { name: "New chat" }).click();
	await expect(panel(page).getByTestId("cob-turn")).toHaveCount(0);

	await panel(page).getByRole("button", { name: "Chat history" }).click();
	await panel(page)
		.getByRole("button", { name: /^Open chat: How do I embed/ })
		.click();
	await expect(panel(page).getByTestId("cob-turn")).toContainText("How do I embed the widget?");
	await expect(panel(page).getByTestId("cob-turn")).toContainText("copy the snippet");

	await panel(page).getByRole("button", { name: "Chat history" }).click();
	page.once("dialog", (d) => d.accept());
	await panel(page)
		.getByRole("button", { name: /^Delete chat: How do I embed/ })
		.click();
	await expect(panel(page).getByText("No chats yet")).toBeVisible();
});

test("search mode shows the banner and linked results, without thumbs", async ({ page }, info) => {
	await signIn(page, roleOf(info));
	await launcher(page).click();
	const box = page.getByRole("textbox", { name: "Ask COB" });
	await box.fill("cap please");
	await box.press("Enter");
	const results = panel(page).getByTestId("cob-search-results");
	await expect(results).toContainText("You've used this month's questions");
	await expect(results.getByRole("link", { name: "Open" })).toBeVisible();
	await expect(panel(page).getByRole("button", { name: "Good answer" })).toHaveCount(0);
});

test("desktop: the width is kept across a reload", async ({ page }, info) => {
	test.skip(isPhone(page), "the phone panel is a full-screen sheet");
	await signIn(page, roleOf(info));
	await launcher(page).click();
	const handle = page.getByRole("separator", { name: "Resize COB panel" });
	await handle.focus();
	await page.keyboard.press("ArrowLeft");
	await page.keyboard.press("ArrowLeft");
	await page.keyboard.press("ArrowLeft");
	await expect(handle).toHaveAttribute("aria-valuenow", "480");
	await page.reload();
	await launcher(page).click();
	await expect(page.getByRole("separator", { name: "Resize COB panel" })).toHaveAttribute("aria-valuenow", "480");
});

test("phone: a full-screen sheet that keeps focus inside and returns it on close", async ({ page }, info) => {
	test.skip(!isPhone(page), "desktop widths dock the panel");
	await signIn(page, roleOf(info));
	await launcher(page).click();
	const dialog = panel(page);
	await expect(dialog).toBeVisible();
	const box = await dialog.boundingBox();
	expect(box?.width).toBeGreaterThanOrEqual(388);
	for (let i = 0; i < 12; i++) {
		await page.keyboard.press("Tab");
		expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
	}
	await page.keyboard.press("Escape");
	await expect(dialog).toBeHidden();
	await expect(launcher(page)).toBeFocused();
});

// COB5: actions are cards the person confirms ------------------------------------------------------

async function ask(page: Page, text: string) {
	await launcher(page).click();
	const box = page.getByRole("textbox", { name: "Ask COB" });
	await box.fill(text);
	await box.press("Enter");
	return panel(page).getByTestId("cob-action-card");
}

test("a request becomes a card; Confirm runs it and the result replaces the buttons", async ({ page }, info) => {
	await signIn(page, roleOf(info));
	const card = await ask(page, "create a widget key for shreelabs.in");
	await expect(card).toBeVisible();
	await expect(card.getByTestId("cob-action-risk")).toHaveText("Low risk");
	await expectNoAxeViolations(page, "cob action card");
	await card.getByRole("button", { name: "Confirm" }).click();
	await expect(card).toContainText("Widget key Main site created");
	await expect(card.getByRole("button", { name: "Confirm" })).toHaveCount(0);
	await card.getByRole("link", { name: "Open Website widget →" }).click();
	await expect(page).toHaveURL(/\/widget/);
	await expect(panel(page)).toBeVisible();
});

test("deleting a knowledge base needs its name typed exactly", async ({ page }, info) => {
	await signIn(page, roleOf(info));
	const card = await ask(page, "delete the Shree Labs knowledge base");
	await expect(card.getByTestId("cob-action-risk")).toHaveText("Can't be undone");
	const del = card.getByRole("button", { name: "Delete" });
	await expect(del).toBeDisabled();
	await card.getByLabel(/Type Shree Labs to confirm/).fill("Shree Lab");
	await expect(del).toBeDisabled();
	await card.getByLabel(/Type Shree Labs to confirm/).fill("Shree Labs");
	await expect(del).toBeEnabled();
	await del.click();
	await expect(card).toContainText("Knowledge base Shree Labs deleted");
});

test("a rotated key is shown once and gone after Hide", async ({ page }, info) => {
	await signIn(page, roleOf(info));
	const card = await ask(page, "rotate my Zapier key");
	await card.getByRole("button", { name: "Confirm" }).click();
	await expect(card.getByText("tk_E2E_SECRET_123")).toBeVisible();
	await card.getByRole("button", { name: "I've copied it" }).click();
	await expect(card.getByText("tk_E2E_SECRET_123")).toHaveCount(0);
});

test("a card past its time says it expired and offers to ask again", async ({ page }, info) => {
	await signIn(page, roleOf(info));
	const card = await ask(page, "expired widget key please");
	await expect(card).toContainText("This card expired. Ask me again.");
	await expect(card.getByRole("button", { name: "Confirm" })).toHaveCount(0);
	await expect(card.getByRole("button", { name: "Ask again" })).toBeVisible();
});
