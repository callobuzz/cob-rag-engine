import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * BIZ2: keeping a learned website fresh, on the mock engine (e2e/fixtures/mock-website.ts).
 * Knowledge → From your website: learning progress turns into "Learned", an owner leaves a page
 * out, changes the schedule, makes and copies the update link; a member sees it all but cannot
 * change it. Indian demo data: Shree Dental Clinic, Pune.
 */
let world: World;
const PATIENT = "7b221003-0000-4000-8000-000000000001";

async function openWebsiteTab(page: Page) {
	if (isPhone(page) && !(await page.getByRole("navigation", { name: "Main" }).isVisible())) {
		await page.getByRole("button", { name: "Open menu" }).click();
	}
	await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Knowledge" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Knowledge" })).toBeVisible();
	await page.getByRole("tab", { name: "From your website" }).click();
	await expect(page.getByRole("article", { name: "shreedental.in" })).toBeVisible();
}

test.beforeEach(async ({ context }) => {
	world = createWorld();
	await installMockEngine(context, world);
});

test("learning progress turns into Learned", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role === "superadmin", "business pages");
	const kb = world.website.kbs.get(PATIENT);
	const source = kb?.sources[0];
	const waiting = kb?.pages.find((p) => p.url.endsWith("/pricing"));
	if (!kb || !source || !waiting) throw new Error("the mock has no website");
	source.status = "discovering";
	source.pages_learned = 2;
	waiting.status = "queued";
	await logIn(page, role);
	await openWebsiteTab(page);
	const card = page.getByRole("article", { name: "shreedental.in" });
	await expect(card).toContainText("Learning your pages…");
	const row = page.getByRole("row", { name: /Fees and charges/ });
	await expect(row).toContainText("Waiting");
	await expect(card).toContainText("Up to date", { timeout: 20_000 });
	await expect(row).toContainText("Learned", { timeout: 20_000 });
	await fullShot(page, screenshotPath(info, "website-learned"));
});

test("an owner leaves a page out, picks a weekly schedule and makes the update link", async ({
	page,
	context,
}, info) => {
	const role = roleOf(info);
	test.skip(role !== "owner", "owner controls");
	await context.grantPermissions(["clipboard-read", "clipboard-write"]).catch(() => undefined);
	await logIn(page, role);
	await openWebsiteTab(page);

	const row = page.getByRole("row", { name: /Fees and charges/ });
	await row.getByRole("button", { name: "Actions for Fees and charges" }).click();
	await page.getByRole("menuitem", { name: "Leave out" }).click();
	await expect(row).toContainText("Excluded by you");

	const select = page.getByLabel("Check my website");
	await expect(select).toHaveValue("daily");
	await select.selectOption("weekly");
	await expect(select).toHaveValue("weekly");
	expect(world.website.writes).toContainEqual(
		expect.objectContaining({ method: "PATCH", body: { refresh_mode: "weekly" } }),
	);
	await select.selectOption("off");
	await expect(page.getByRole("checkbox", { name: /check pages that change often/i })).toBeDisabled();
	await select.selectOption("daily");

	await page.getByRole("button", { name: "Create update link" }).click();
	const link = page.getByTestId("push-link");
	await expect(link).toContainText("/api/v1/public/learn/lk_");
	await expect(link).toContainText("not shown again");
	await link.getByRole("button", { name: "Copy Your update link" }).click();
	await expect(link.getByRole("button", { name: "Copy Your update link" })).toContainText("Copied");
	await expect(page.getByTestId("push-guides")).toContainText("transition_post_status");
	await expectNoAxeViolations(page, "website-freshness");
	await fullShot(page, screenshotPath(info, "website-freshness"));

	await page.reload();
	await page.getByRole("tab", { name: "From your website" }).click();
	await expect(page.getByText(/an update link is set up/i)).toBeVisible();
	await expect(page.getByTestId("push-link")).toHaveCount(0);
});

test("a member sees the schedule and the link but cannot change them", async ({ page }, info) => {
	const role = roleOf(info);
	test.skip(role !== "member", "member view");
	const kb = world.website.kbs.get(PATIENT);
	if (!kb) throw new Error("the mock has no website");
	kb.learn_key = { set: true, hint: "ab12", created_at: "2026-10-05T10:00:00Z" };
	await logIn(page, role);
	await openWebsiteTab(page);
	await expect(page.getByLabel("Check my website")).toBeDisabled();
	await expect(page.getByLabel("Check my website")).toHaveValue("daily");
	await expect(page.getByText(/it ends in ab12/)).toBeVisible();
	await expect(page.getByRole("button", { name: "Create a new link" })).toHaveAttribute("aria-disabled", "true");
	await expect(page.getByRole("button", { name: "Remove link" })).toHaveAttribute("aria-disabled", "true");
	expect(world.website.writes).toEqual([]);
});
