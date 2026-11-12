import { expect, type Page, test } from "@playwright/test";
import { ACCOUNTS, createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { scraperOf } from "./fixtures/mock-scraper";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * D8 crawler on the mock engine + mock scraper (e2e/fixtures/mock-scraper.ts), across the README
 * matrix. The mock "crawls" the Vidya Classes fixture site in e2e/fixtures/site by path, follows
 * the scraper's rules after D8 part A (Bearer or key, review mode required for sign-ins) and turns
 * delivered pages into mock-engine documents under Shree Labs' document limit.
 * The same journeys against the running stack are pending at the bottom of the file.
 */

const ADMISSIONS = "91f0e6b2-0000-4000-8000-000000000004";
const SITE = "https://vidyaclasses.in/";
const SITEMAP = "https://vidyaclasses.in/sitemap.xml";

let world: World;

/** "Admissions help" belongs to Shree Labs here, with room for two more documents (A6). */
function crawlerWorld(): World {
	const w = createWorld({ docLimit: 4 });
	const shree = w.tenants.find((t) => t.name === "Shree Labs");
	const kb = w.kbs.items.find((k) => k.id === ADMISSIONS);
	if (shree && kb) kb.tenant_id = shree.id;
	return w;
}

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		const widths = await page.evaluate(() => ({
			doc: document.documentElement.scrollWidth,
			main: document.getElementById("main")?.getBoundingClientRect().width ?? 0,
		}));
		expect(widths.doc, name).toBeLessThanOrEqual(390);
		expect(widths.main, name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

test.describe("crawler", () => {
	test.beforeEach(async ({ context, page }) => {
		world = crawlerWorld();
		await installMockEngine(context, world);
		await page.emulateMedia({ reducedMotion: "reduce" });
	});

	test("crawl a site, review, deliver with a plan-limit error, retry, see the documents", async ({ page }, info) => {
		test.setTimeout(150_000);
		await logIn(page, roleOf(info));

		await page.goto(`/crawler?kb=${ADMISSIONS}`);
		await expect(page.getByRole("heading", { level: 1, name: "Crawler" })).toBeVisible();
		await expect(page.getByText("Crawl a website, review the pages, then add them to Admissions help.")).toBeVisible();
		await expect(page.getByRole("heading", { name: "Bring a website into Admissions help" })).toBeVisible();
		await checkPage(page, "crawler-empty");

		// New crawl: a signed-in person can't turn review mode off.
		await page.getByRole("button", { name: "New crawl" }).first().click();
		const dialog = page.getByRole("dialog", { name: "New crawl" });
		const review = dialog.getByRole("switch", { name: "Review pages before they go in" });
		await expect(review).toBeChecked();
		await expect(review).toBeDisabled();
		await expect(dialog).toContainText("Always on when you're signed in");
		await expect(dialog).toContainText("Private network addresses");
		await expect(dialog.getByLabel("Knowledge base")).toHaveValue(ADMISSIONS);
		await dialog.getByRole("button", { name: "Start crawl" }).click();
		await expect(dialog.getByText("Enter a sitemap URL or at least one page address.")).toBeVisible();
		await dialog.getByLabel("Sitemap URL").fill(SITEMAP);
		await dialog.getByLabel("Page addresses").fill(SITE);
		await dialog.getByLabel("Page limit").fill("10");
		await expectNoAxeViolations(page, "new crawl dialog");
		await fullShot(page, screenshotPath(info, "crawler-new"));
		await dialog.getByRole("button", { name: "Start crawl" }).click();
		await expect(dialog).toBeHidden();

		// The job page: crawling, then ready for review with four verified pages and one rejected.
		await expect(page.getByRole("heading", { level: 1, name: "vidyaclasses.in" })).toBeVisible();
		const approveAll = page.getByRole("button", { name: "Approve all verified (4)" });
		await expect(approveAll).toBeVisible({ timeout: 20_000 });
		const summary = page.getByRole("region", { name: "Summary" });
		await expect(summary.getByText("Ready for review")).toBeVisible();
		await expect(summary).toContainText("5 of 5 done");
		const chips = page.getByRole("group", { name: "Filter pages by status" });
		await expect(chips.getByRole("button", { name: "Verified 4" })).toBeVisible();
		await expect(chips.getByRole("button", { name: "Rejected 1" })).toBeVisible();
		await expect(page.getByText("Too few words")).toBeVisible();
		expect(
			scraperOf(world)
				.calls.filter((c) => c.path === "/api/v1/scrape")
				.map((c) => c.auth),
		).toEqual(["bearer"]);
		await checkPage(page, "crawler-job");

		// Read a page: a dialog named by its title, the text preformatted in a focusable region.
		await page.getByRole("button", { name: "Open Fees 2026-27 | Vidya Classes" }).click();
		const drawer = page.getByRole("dialog", { name: "Fees 2026-27 | Vidya Classes" });
		const text = drawer.getByRole("region", { name: "Extracted text of Fees 2026-27 | Vidya Classes" });
		await expect(text).toContainText("Class 10 (SSC or CBSE), with board exam practice papers: ₹38,000 a year.");
		await text.focus();
		await expect(text).toBeFocused();
		await expectNoAxeViolations(page, "page drawer");
		await fullShot(page, screenshotPath(info, "crawler-page"));
		await page.keyboard.press("Escape");
		await expect(drawer).toBeHidden();

		// Approve all, deliver: two pages fit the plan, two are refused and stay approved.
		await approveAll.click();
		const deliver = page.getByRole("button", { name: "Deliver 4 approved pages" });
		await expect(deliver).toBeEnabled();
		await deliver.click();
		const result = page.getByTestId("deliver-result");
		await expect(result).toContainText("Delivered 2 of 4 pages.");
		await expect(result).toContainText("2 failed and stay approved with their error.");
		await expect(page.getByTestId("delivery-error")).toHaveCount(2);
		await expect(page.getByTestId("delivery-error").first()).toContainText("Document limit reached (4)");
		const retry = page.getByRole("button", { name: "Retry delivery (2)" });
		await expect(retry).toBeVisible();
		await checkPage(page, "crawler-delivery-error");

		// The owner makes room on the plan; retry sends only the two that failed.
		const shree = world.tenants.find((t) => t.name === "Shree Labs");
		world.docs.limits.delete(shree?.id ?? "");
		await retry.click();
		await expect(result).toContainText("Delivered 2 of 2 pages.");
		await expect(result).toContainText("Every approved page is in Admissions help.");
		await expect(page.getByTestId("delivery-error")).toHaveCount(0);

		// The pages are documents of the knowledge base now.
		await page.goto(`/documents?kb=${ADMISSIONS}`);
		await expect(page.getByRole("link", { name: "fees-2026-27-vidya-classes.md" })).toBeVisible();
		await expect(page.getByRole("link", { name: "refund-policy-vidya-classes.md" })).toBeVisible();

		// Back on the list: the crawl, then delete it (documents stay).
		await page.goto(`/crawler?kb=${ADMISSIONS}`);
		const table = page.getByRole("table", { name: "Crawls" });
		await expect(table.getByRole("rowheader", { name: "vidyaclasses.in" })).toBeVisible();
		await expect(table.getByText("5 / 5")).toBeVisible();
		await checkPage(page, "crawler-list");
		await table.getByRole("button", { name: "Delete crawl of vidyaclasses.in" }).click();
		const confirm = page.getByRole("alertdialog", { name: "Delete this crawl?" });
		await expect(confirm).toContainText("Pages already delivered stay in the knowledge base.");
		await confirm.getByRole("button", { name: "Delete crawl" }).click();
		await expect(page.getByRole("heading", { name: "Bring a website into Admissions help" })).toBeVisible();
		expect(world.docs.docs.filter((d) => d.corpusId === ADMISSIONS)).toHaveLength(4);
	});

	test("an admin API key may turn review mode off: pages go in when the crawl ends", async ({ page }, info) => {
		test.skip(roleOf(info) !== "superadmin", "key sign-in: once per theme/viewport, on the admin projects");
		test.setTimeout(90_000);
		await page.goto("/login/key");
		await page.getByLabel("API key").fill(ACCOUNTS.adminKey);
		await page.getByRole("button", { name: "Sign in" }).click();
		await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

		await page.goto(`/crawler?kb=${ADMISSIONS}`);
		await page.getByRole("button", { name: "New crawl" }).first().click();
		const dialog = page.getByRole("dialog", { name: "New crawl" });
		const review = dialog.getByRole("switch", { name: "Review pages before they go in" });
		await expect(review).toBeEnabled();
		await expect(review).toBeChecked();
		await review.click();
		await expect(review).not.toBeChecked();
		await expect(dialog).toContainText("Off: every verified page is added as soon as the crawl ends.");
		await dialog.getByLabel("Page addresses").fill("https://vidyaclasses.in/fees\nhttps://vidyaclasses.in/batches");
		await dialog.getByRole("button", { name: "Start crawl" }).click();

		await expect(page.getByRole("heading", { level: 1, name: "vidyaclasses.in/fees" })).toBeVisible();
		const summary = page.getByRole("region", { name: "Summary" });
		await expect(summary.getByText("Done")).toBeVisible({ timeout: 20_000 });
		await expect(summary).toContainText("Off — verified pages were added automatically");
		await expect(page.getByRole("group", { name: "Filter pages by status" })).toContainText("Delivered 2");
		expect(scraperOf(world).calls.find((c) => c.path === "/api/v1/scrape")?.auth).toBe("api_key");
		await checkPage(page, "crawler-key-auto");
		await page.goto(`/documents?kb=${ADMISSIONS}`);
		await expect(page.getByRole("link", { name: "batches-and-timings-vidya-classes.md" })).toBeVisible();
	});

	test("an unknown crawl says so", async ({ page }, info) => {
		await logIn(page, roleOf(info));
		await page.goto("/crawler/nosuchjob123");
		await expect(page.getByRole("heading", { level: 1, name: "Crawl job" })).toBeVisible();
		await expect(page.getByRole("heading", { name: "Crawl not found" })).toBeVisible();
		await checkPage(page, "crawler-not-found");
	});
});

/**
 * Pending: the same journeys against the running stack (engine + rebuilt scraper with D8 part A
 * and SCRAPER_ALLOW_PRIVATE_NETWORKS=true, the fixture site served from e2e/fixtures/site where
 * the scraper container can reach it). Enable with E2E_SCRAPER=1 once that stack exists.
 */
test.describe("crawler on the real stack", () => {
	test("owner crawls the fixture site, delivers and sees the documents", async () => {
		test.skip(
			!process.env.E2E_SCRAPER,
			"needs the running stack with the rebuilt scraper (D8 part A) and SCRAPER_ALLOW_PRIVATE_NETWORKS=true",
		);
		test.fixme(true, "real-stack crawler journey: pending until the scraper container is rebuilt");
	});
});
