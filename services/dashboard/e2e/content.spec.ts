import { fileURLToPath } from "node:url";
import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * D4 content on the mock engine (e2e/fixtures/mock-documents.ts), across the README matrix:
 * documents, detail with passages and versions, restore, uploads through live stages, the
 * near-duplicate review, records import with mapping and re-import, member gating and the plan's
 * document cap. The mock follows A5 (`owner_required`) and A6 (`document_limit_reached`); the
 * same journeys against the real engine are pending until A5/A6 are merged (bottom of file).
 */

const PATIENT = "7b221003-0000-4000-8000-000000000001";
const STAFF = "7b2210a9-0000-4000-8000-000000000002";
const ADMISSIONS = "91f0e6b2-0000-4000-8000-000000000004";
const file = (name: string) => fileURLToPath(new URL(`./fixtures/files/${name}`, import.meta.url));
const PRICE = file("test-price-list-oct.pdf");
const POLICY = file("home-collection-policy.docx");
const FAQ = file("faq.csv");

let world: World;

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

const main = (page: Page) => page.locator("main");
const uploadRow = (page: Page, name: string) => page.getByRole("listitem", { name, exact: true });

async function waitDone(page: Page, name: string) {
	await expect(uploadRow(page, name).getByText("Done", { exact: true })).toBeVisible({ timeout: 20_000 });
}

test.describe("content", () => {
	test.beforeEach(async ({ context, page }, info) => {
		world = createWorld(info.title.includes("plan cap") ? { docLimit: 2 } : {});
		await installMockEngine(context, world);
		// No half-finished open/close animations between steps (axe, screenshots).
		await page.emulateMedia({ reducedMotion: "reduce" });
	});

	test("documents, detail, add content and jobs render with PoweredBy, axe clean, screenshots", async ({
		page,
	}, info) => {
		test.setTimeout(180_000);
		const role = roleOf(info);
		await logIn(page, role);

		await page.goto(`/documents?kb=${PATIENT}`);
		await expect(page.getByRole("heading", { level: 1, name: "Documents" })).toBeVisible();
		const table = page.getByRole("table", { name: "Documents in this knowledge base" });
		await expect(table.getByRole("link", { name: "test-prep-guide.pdf" })).toBeVisible();
		await expect(main(page).getByText("Patient help · 2 documents")).toBeVisible();
		await checkPage(page, "documents");

		await page.goto(`/documents?kb=${role === "superadmin" ? ADMISSIONS : STAFF}`);
		await expect(page.getByRole("heading", { name: /something to know$/ })).toBeVisible();
		await checkPage(page, "documents-empty");

		await page.goto(`/documents?kb=${PATIENT}`);
		await page.getByRole("link", { name: "test-prep-guide.pdf" }).click();
		await expect(page.getByRole("heading", { level: 1, name: "test-prep-guide.pdf" })).toBeVisible();
		await expect(page.getByRole("list", { name: /Passages of version 2/ })).toBeVisible();
		await checkPage(page, "document-passages");
		await page.getByRole("tab", { name: /Versions/ }).click();
		await expect(page.getByRole("table", { name: "Versions of this document" })).toBeVisible();
		await checkPage(page, "document-versions");
		await page.getByRole("button", { name: "Document actions" }).click();
		await expect(page.getByRole("menuitem", { name: /Delete document/ })).toBeVisible();
		await expectNoAxeViolations(page, "actions menu");
		await page.keyboard.press("Escape");
		await expect(page.getByRole("menu")).toHaveCount(0);

		await page.goto(`/add?kb=${PATIENT}`);
		await expect(page.getByRole("heading", { level: 1, name: "Add content" })).toBeVisible();
		await expect(page.getByText(/Accepted: \.csv, \.docx/)).toBeVisible();
		await checkPage(page, "add-upload");
		await page.getByTestId("file-input").setInputFiles([PRICE]);
		await expect(uploadRow(page, "test-price-list-oct.pdf").getByLabel("How to add it")).toBeVisible();
		await checkPage(page, "add-upload-ready");

		await page.getByRole("tab", { name: "Import records" }).click();
		await expect(page.getByLabel("Paste JSON")).toBeVisible();
		await checkPage(page, "add-records");
		await page.getByRole("tab", { name: "Jobs" }).click();
		await expect(page.getByRole("table", { name: "Ingestion jobs" })).toBeVisible();
		await checkPage(page, "add-jobs");
	});

	test("owner: upload with live stages, review → replace, restore, records import and re-import", async ({
		page,
	}, info) => {
		test.skip(roleOf(info) !== "owner", "owner journey");
		test.setTimeout(180_000);
		await logIn(page, "owner");
		await page.goto(`/add?kb=${PATIENT}`);
		await page.getByTestId("file-input").setInputFiles([PRICE, POLICY]);
		await page.getByRole("button", { name: "Upload 2 files" }).click();
		const priceRow = uploadRow(page, "test-price-list-oct.pdf");
		await expect(priceRow.getByRole("progressbar")).toBeVisible();
		await expect(priceRow.getByText(/Reading|Splitting|Enriching|Indexing|Saving/).first()).toBeVisible({
			timeout: 15_000,
		});
		await expectNoAxeViolations(page, "stages");
		await fullShot(page, screenshotPath(info, "upload-stages"));
		await waitDone(page, "test-price-list-oct.pdf");
		await waitDone(page, "home-collection-policy.docx");
		await page.getByRole("button", { name: "Clear finished" }).click();
		await expect(page.getByTestId("upload-row")).toHaveCount(0);

		// The same price list again, automatic mode → it looks like the existing one.
		await page.getByTestId("file-input").setInputFiles([PRICE]);
		await page.getByRole("button", { name: "Upload 1 file" }).click();
		const review = uploadRow(page, "test-price-list-oct.pdf").getByTestId("pending-review");
		await expect(review).toBeVisible({ timeout: 20_000 });
		await expect(review).toContainText("Possible duplicate");
		await expect(review).toContainText(
			"looks like test-price-list-oct.pdf, which is already in this knowledge base (96% similar)",
		);
		await expectNoAxeViolations(page, "review card");
		await fullShot(page, screenshotPath(info, "upload-review"));
		await review.getByRole("button", { name: "Replace test-price-list-oct.pdf" }).click();
		await waitDone(page, "test-price-list-oct.pdf");

		await page.goto(`/documents?kb=${PATIENT}`);
		await page.getByRole("link", { name: "test-price-list-oct.pdf" }).click();
		await page.getByRole("tab", { name: /Versions/ }).click();
		const versions = page.getByRole("table", { name: "Versions of this document" });
		const rows = versions.locator("tbody tr");
		await expect(rows).toHaveCount(2);
		await expect(rows.nth(0)).toContainText("v2");
		await expect(rows.nth(0)).toContainText("Active");
		await versions.getByRole("button", { name: "Make active: v1" }).click();
		await expect(page.getByText("Version 1 is now active")).toBeVisible();
		await expect(rows.nth(1)).toContainText("Active");

		// Records: faq.csv with Hindi and English questions, id column faq_id.
		await page.goto(`/add?kb=${PATIENT}&tab=records`);
		await page.getByTestId("records-file").setInputFiles(FAQ);
		await expect(page.getByRole("combobox", { name: /^ID/ })).toHaveValue("faq_id");
		await expect(page.getByRole("combobox", { name: /^Text/ })).toHaveValue("answer");
		await expect(page.getByRole("combobox", { name: "Title" })).toHaveValue("question");
		await expectNoAxeViolations(page, "records mapping");
		await fullShot(page, screenshotPath(info, "records-mapping"));
		await page.getByRole("button", { name: "Next" }).click();
		await expect(page.getByTestId("records-count")).toHaveText("5 records in 1 batch");
		await expect(page.getByRole("table", { name: "The first 5 records as they will be sent" })).toContainText(
			"रिपोर्ट कब मिलेगी?",
		);
		await expectNoAxeViolations(page, "records preview");
		await fullShot(page, screenshotPath(info, "records-preview"));
		await page.getByRole("button", { name: "Import 5 records" }).click();
		await expect(page.getByTestId("records-counts")).toHaveText("5 new · 0 updated · 0 unchanged", { timeout: 15_000 });
		await fullShot(page, screenshotPath(info, "records-imported"));

		await page.goto(`/documents?kb=${PATIENT}&q=faq-`);
		const table = page.getByRole("table", { name: "Documents in this knowledge base" });
		await expect(table.getByText("faq-002")).toBeVisible();
		await expect(table.getByRole("link", { name: "रिपोर्ट कब मिलेगी?" })).toBeVisible();
		await expect(table.getByText("record").first()).toBeVisible();

		await page.goto(`/add?kb=${PATIENT}&tab=records`);
		await page.getByTestId("records-file").setInputFiles(FAQ);
		await page.getByRole("button", { name: "Next" }).click();
		await page.getByRole("button", { name: "Import 5 records" }).click();
		await expect(page.getByTestId("records-counts")).toHaveText("0 new · 0 updated · 5 unchanged", { timeout: 15_000 });
	});

	test("member: uploads, but delete and merge stay locked", async ({ page }, info) => {
		test.skip(roleOf(info) !== "member", "member gating");
		test.setTimeout(90_000);
		await logIn(page, "member");
		await page.goto(`/add?kb=${PATIENT}`);
		await page.getByTestId("file-input").setInputFiles([POLICY]);
		await page.getByRole("button", { name: "Upload 1 file" }).click();
		await waitDone(page, "home-collection-policy.docx");
		await page.goto(`/documents?kb=${PATIENT}`);
		await page.getByRole("link", { name: "home-collection-policy.docx" }).click();
		await page.getByRole("button", { name: "Document actions" }).click();
		const del = page.getByRole("menuitem", { name: /Delete document/ });
		await expect(del).toHaveAttribute("aria-disabled", "true");
		await expect(del).toContainText("Only owners can delete documents");
		await expect(page.getByRole("menuitem", { name: /Merge/ })).toHaveAttribute("aria-disabled", "true");
		// aria-disabled (never silently disabled): the click still reaches the gate and shows the toast.
		await del.click({ force: true });
		await expect(page.getByText("Only owners can delete documents").first()).toBeVisible();
		await expect(page.getByRole("alertdialog")).toHaveCount(0);
		await checkPage(page, "document-member");
	});

	test("owner: delete a document with a plain confirmation", async ({ page }, info) => {
		test.skip(roleOf(info) !== "owner", "owner journey");
		await logIn(page, "owner");
		await page.goto(`/documents?kb=${PATIENT}`);
		await page.getByRole("link", { name: "home-collection.pdf" }).click();
		await page.getByRole("button", { name: "Document actions" }).click();
		await page.getByRole("menuitem", { name: /Delete document/ }).click();
		const dialog = page.getByRole("alertdialog", { name: "Delete home-collection.pdf?" });
		await expect(page.getByRole("menu")).toHaveCount(0);
		await expectNoAxeViolations(page, "delete dialog");
		await dialog.getByRole("button", { name: "Delete document" }).click();
		await expect(page.getByText("Deleted home-collection.pdf")).toBeVisible();
		await expect(page.getByRole("heading", { level: 1, name: "Documents" })).toBeVisible();
		await expect(page.getByRole("link", { name: "home-collection.pdf" })).toHaveCount(0);
	});

	test("owner: skip a possible duplicate, with a plain confirmation", async ({ page }, info) => {
		test.skip(roleOf(info) !== "owner", "owner journey");
		test.setTimeout(120_000);
		await logIn(page, "owner");
		await page.goto(`/add?kb=${PATIENT}`);
		await page.getByTestId("file-input").setInputFiles([PRICE]);
		await page.getByRole("button", { name: "Upload 1 file" }).click();
		await waitDone(page, "test-price-list-oct.pdf");
		await page.getByRole("button", { name: "Clear finished" }).click();

		await page.getByTestId("file-input").setInputFiles([PRICE]);
		await page.getByRole("button", { name: "Upload 1 file" }).click();
		const row = uploadRow(page, "test-price-list-oct.pdf");
		const review = row.getByTestId("pending-review");
		await expect(review).toBeVisible({ timeout: 20_000 });
		await review.getByRole("button", { name: "Skip (don't add)" }).click();
		const dialog = page.getByRole("alertdialog", { name: "Skip test-price-list-oct.pdf?" });
		await expect(dialog).toContainText("The upload is removed and nothing is added.");
		await expectNoAxeViolations(page, "skip dialog");
		await dialog.getByRole("button", { name: "Skip", exact: true }).click();
		await expect(row.getByText("Skipped", { exact: true })).toBeVisible({ timeout: 15_000 });
		await expect(review).toHaveCount(0);

		await page.goto(`/documents?kb=${PATIENT}&q=test-price-list`);
		await expect(page.getByRole("link", { name: "test-price-list-oct.pdf" })).toHaveCount(1);
	});

	test("plan cap: a third document shows document_limit_reached", async ({ page }, info) => {
		test.skip(roleOf(info) !== "owner", "tenant owner with max_documents 2");
		await logIn(page, "owner");
		await page.goto(`/add?kb=${PATIENT}`);
		await page.getByTestId("file-input").setInputFiles([POLICY]);
		await page.getByRole("button", { name: "Upload 1 file" }).click();
		const row = uploadRow(page, "home-collection-policy.docx");
		await expect(row.getByText("Your plan allows 2 documents (2 used; this adds 1).")).toBeVisible();
		await expect(row.getByRole("link", { name: "See plan" })).toBeVisible();
		await expectNoAxeViolations(page, "document limit");
		await fullShot(page, screenshotPath(info, "document-limit"));
	});

	test("super admin: uploads into a customer's knowledge base", async ({ page }, info) => {
		test.skip(roleOf(info) !== "superadmin", "admin journey");
		test.setTimeout(90_000);
		await logIn(page, "superadmin");
		await page.goto(`/add?kb=${PATIENT}`);
		await page.getByTestId("file-input").setInputFiles([POLICY]);
		await page.getByRole("button", { name: "Upload 1 file" }).click();
		await waitDone(page, "home-collection-policy.docx");
		await page.getByRole("tab", { name: "Jobs" }).click();
		await expect(
			page.getByRole("table", { name: "Ingestion jobs" }).getByText("home-collection-policy.docx"),
		).toBeVisible();
	});
});

/**
 * The same journeys against the real engine need A5 (`owner_required` on document delete and
 * merge) and A6 (`document_limit_reached`, `max_documents`) merged and the stack rebuilt.
 */
test.describe("content on the real engine", () => {
	test.fixme("owner/member gating and the document cap against the running stack (needs A5 + A6)", async () => {});
});
