import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * LP4: the operator builds and runs the public website, on the mock engine
 * (e2e/fixtures/mock-landing.ts). Owners and members never reach the page. Indian demo data:
 * Sharma Dental Clinic, Pune.
 */
let world: World;
const POSTER = { name: "poster.png", mimeType: "image/png", buffer: Buffer.from("poster") };
const VIDEO = { name: "demo.mp4", mimeType: "video/mp4", buffer: Buffer.from("video-bytes") };

test.beforeEach(async ({ context }) => {
	world = createWorld();
	const operator = world.tenants.find((t) => t.isOperator);
	world.kbs.items.push({
		id: "7b221003-0000-4000-8000-0000000000f1",
		name: "Sahayak help centre",
		description: null,
		tenant_id: operator?.id ?? "",
		created_at: "2026-09-01T10:00:00Z",
		document_count: 4,
		chunk_count: 60,
		last_activity_at: null,
		llm_config: null,
		prompt_config: null,
		retrieval_config: null,
		embedding_config: null,
		applied_embedding: null,
	});
	await installMockEngine(context, world);
});

async function openEditor(page: Page) {
	await page.goto("/admin/landing");
	await expect(page.getByRole("heading", { level: 1, name: "Public website" })).toBeVisible();
	await expect(page.getByTestId("landing-on-state")).toHaveText("Website is off");
}

const section = (page: Page, id: string) => page.getByTestId(`section-${id}`);
const tab = (page: Page, name: string) => page.getByRole("tab", { name, exact: true }).click();

/** OS4: every card ends inside the screen. A wide child once stretched the editor's grid past a
 * phone's edge inside a clipped container, which the page's own width does not show. */
async function expectCardsInside(page: Page) {
	const overflow = await page.evaluate(() =>
		Array.from(document.querySelectorAll('[data-testid="landing-status"], [data-slot="card"]'))
			.map((el) => Math.round(el.getBoundingClientRect().right))
			.filter((right) => right > window.innerWidth + 1),
	);
	expect(overflow).toEqual([]);
}

test("the operator edits, previews, publishes and switches the website on", async ({ page }, info) => {
	test.skip(roleOf(info) !== "superadmin", "the operator's page");
	await logIn(page, "superadmin");
	await openEditor(page);
	if (!isPhone(page)) await expect(page.getByRole("link", { name: "Public website" })).toBeVisible();
	if (isPhone(page)) await expectCardsInside(page);
	await fullShot(page, screenshotPath(info, "landing-1-start"));

	// a headline in two languages: switch हिंदी on, then write the headline in both
	await tab(page, "Languages");
	await page.getByRole("switch", { name: "Offer the website in हिंदी" }).click();
	await expect(page.getByTestId("missing-hi")).toContainText("texts still missing in हिंदी");
	await tab(page, "Home page");
	await section(page, "hero").getByRole("button", { name: "Edit Top of the page" }).click();
	const headline = page.getByTestId("field-home.hero.headline");
	await headline.getByRole("textbox").fill("Answers at midnight, in your language");
	await headline.getByRole("button", { name: /हिंदी/ }).click();
	await expect(headline).toContainText("Missing. Needed before publishing.");
	await headline.getByRole("textbox").fill("आधी रात को भी जवाब, आपकी भाषा में");
	await expect(headline).not.toContainText("Missing. Needed before publishing.");
	await headline.getByRole("button", { name: /English/ }).click();
	await expect(headline.getByRole("textbox")).toHaveValue("Answers at midnight, in your language");

	// reorder a section with the buttons
	await section(page, "how-it-works").getByRole("button", { name: "Move How it works down" }).click();
	await expect(page.getByTestId("sections-live")).toHaveText("How it works is now number 5 of 16.");

	// a starting example, only some parts
	await page.getByTestId("preset-open").click();
	await page.getByTestId("preset-use-clinic").click();
	await page.getByLabel("The WhatsApp day").uncheck();
	await page.getByTestId("preset-apply").click();
	await expect(page.getByText("You have unsaved changes.").first()).toBeVisible();

	// write the conversation from a knowledge base (mocked) and use it
	await page.getByTestId("generate-open").click();
	await page.locator("#generate-kb").selectOption({ label: "Sahayak help centre" });
	await page.getByTestId("generate-run").click();
	await expect(page.getByTestId("generate-result")).toContainText("Timings › Saturday");
	await page.getByTestId("generate-use").click();

	// the brand colour with the readability report
	await tab(page, "Look");
	await page.getByLabel("Your brand colour", { exact: true }).fill("#7c3aed");
	await expect(page.getByTestId("contrast-report")).toBeVisible();
	await page.getByTestId("font-picker").getByText("Calm and editorial").click();
	await expect(page.getByRole("radio", { name: /Editorial/ })).toBeChecked();
	await fullShot(page, screenshotPath(info, "landing-2-look"));

	// a poster and a video (mocked uploads), then use them in a section
	await page
		.getByTestId("media-video")
		.getByLabel(/Upload a video/)
		.setInputFiles(VIDEO);
	await expect(page.getByTestId("poster-required")).toBeVisible();
	await page
		.getByTestId("media-poster")
		.getByLabel(/Upload a poster/)
		.setInputFiles(POSTER);
	await expect(page.getByTestId("use-video")).toBeEnabled();
	await page.getByTestId("use-video").click();

	// save, then preview at phone and desktop size
	await page.getByTestId("landing-save").click();
	await expect(page.getByText("Draft saved").first()).toBeVisible();
	await page.getByTestId("landing-preview").click();
	await expect(page.getByTestId("preview-phone")).toBeVisible();
	await expect(page.getByTestId("preview-desktop")).toBeVisible();
	await page.getByRole("radio", { name: "Dark" }).click();
	await page.waitForTimeout(400);
	await page.screenshot({ path: screenshotPath(info, "landing-3-preview") });
	await page.keyboard.press("Escape");

	// publishing is refused while हिंदी is incomplete; the list leads to the field
	await page.getByTestId("landing-publish").click();
	await page.getByTestId("landing-publish-confirm").click();
	await expect(page.getByTestId("publish-gaps")).toBeVisible();
	await page.screenshot({ path: screenshotPath(info, "landing-4-gaps") });
	await page.keyboard.press("Escape");
	await tab(page, "Languages");
	await page.getByRole("switch", { name: "Offer the website in हिंदी" }).click();

	// publish with a note, set the address, connect "Ask it yourself", switch on
	await page.getByTestId("landing-publish").click();
	await page.getByLabel("What changed (optional)").fill("First version");
	await page.getByTestId("landing-publish-confirm").click();
	await expect(page.getByText("Last published")).toBeVisible();
	await expect(page.getByRole("switch", { name: "Show the website to visitors" })).toBeDisabled();
	await page.getByTestId("address-card").getByLabel("Website address").fill("https://www.sharmadental.in");
	await page.getByTestId("address-card").getByRole("button", { name: "Save address" }).click();
	await expect(page.getByTestId("landing-address")).toHaveText("https://www.sharmadental.in");
	await page
		.getByTestId("ask-card")
		.getByLabel("Knowledge base the assistant answers from")
		.selectOption({ label: "Sahayak help centre" });
	await page.getByTestId("ask-connect").click();
	await expect(page.getByTestId("ask-status")).toHaveAttribute("data-status", "ready");
	await page.getByRole("switch", { name: "Show the website to visitors" }).click();
	await expect(page.getByTestId("landing-on-state")).toHaveText("Website is on");

	// versions
	await tab(page, "Versions");
	await expect(page.getByTestId("version-1")).toContainText("First version");

	if (isPhone(page)) {
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	}
	await expectNoAxeViolations(page, "admin-landing");
	await fullShot(page, screenshotPath(info, "landing-5-published"));
});

test("owners and members never reach the public website editor", async ({ page }, info) => {
	test.skip(roleOf(info) === "superadmin", "the operator sees it");
	await logIn(page, roleOf(info));
	if (!isPhone(page)) await expect(page.getByRole("link", { name: "Public website" })).toHaveCount(0);
	await page.goto("/admin/landing");
	await expect(page.getByRole("heading", { level: 1, name: "Public website" })).toHaveCount(0);
});
