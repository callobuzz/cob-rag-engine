import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { createPortalState, installPortalMock, type PortalState, seedDraft } from "./fixtures/mock-portal";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, openAdvanced, roleOf, screenshotPath } from "./utils/session";

/**
 * D11 Content Portal on the mock engine (e2e/fixtures/mock-portal.ts on top of mock-engine.ts,
 * with `features.content_portal` on) across the README matrix: prepare content for "Patient help"
 * from pasted text to an ingested document, the member's gating, a post-ingest review published as
 * a new version, the AI chat and audit trail, routing rules, and the portal switched off. The same
 * journeys against the running stack are pending at the bottom of the file.
 */

const PATIENT = "7b221003-0000-4000-8000-000000000001";
const POLICY = [
	"Home collection is available across Pune: Kothrud, Baner, Aundh, Hadapsar and Viman Nagar. Slots run from 6:30 am to 11 am, Monday to Saturday.",
	"Book on +91 98220 12345 or on WhatsApp at least a day ahead. Fasting samples are collected before 9 am.",
	"The visit is free for orders above ₹999; otherwise ₹100 is added. Pay by UPI or cash.",
].join("\n\n");

let world: World;
let state: PortalState;

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		const width = await page.evaluate(() => document.documentElement.scrollWidth);
		expect(width, name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

const main = (page: Page) => page.locator("#main");
const card = (page: Page, n: number) => page.getByRole("article", { name: `Chunk ${n}`, exact: true });

async function startEditing(page: Page) {
	await page.getByRole("button", { name: "Start editing" }).click();
	await expect(page.getByTestId("lock-bar")).toHaveAttribute("data-state", "mine");
}

test.describe("content portal on", () => {
	test.beforeEach(async ({ context, page }) => {
		world = createWorld({ contentPortal: true });
		await installMockEngine(context, world);
		state = createPortalState();
		await installPortalMock(context, world, state);
		await page.emulateMedia({ reducedMotion: "reduce" });
	});

	test("paste → AI chunks → edit, split, approve → submit → approve → ingest → in Documents", async ({
		page,
	}, info) => {
		test.setTimeout(120_000);
		await logIn(page, roleOf(info));
		if (!isPhone(page)) {
			await openAdvanced(page);
			await expect(
				page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Content Portal" }),
			).toBeVisible();
		}
		await page.goto(`/portal?kb=${PATIENT}`);
		await expect(page.getByRole("heading", { level: 1, name: "Content Portal" })).toBeVisible();
		await expect(page.getByRole("heading", { name: "Prepare content for Patient help" })).toBeVisible();
		await checkPage(page, "portal-empty");

		await main(page).getByRole("button", { name: "New workspace" }).first().click();
		const create = page.getByRole("dialog", { name: "New workspace" });
		await create.getByLabel("Title").fill("Home collection policy");
		await expectNoAxeViolations(page, "new workspace dialog");
		await create.getByRole("button", { name: "Create workspace" }).click();
		await expect(page.getByRole("heading", { level: 1, name: "Home collection policy" })).toBeVisible();
		const created = state.bodies.find((b) => b.path === "/workspaces");
		expect(created?.body).not.toHaveProperty("user");

		// Staging: paste the policy; the AI structures it (polled every 3 s).
		await page.getByRole("radio", { name: "Paste text" }).click();
		await page.getByLabel("Text", { exact: true }).fill(POLICY);
		await page.getByLabel("Title (optional)").fill("Home collection policy");
		await page.getByRole("button", { name: "Add", exact: true }).click();
		const staged = page.getByRole("list", { name: "Staged items" });
		await expect(staged.getByText("Structured")).toBeVisible({ timeout: 15_000 });
		await checkPage(page, "portal-staging");

		await page.getByRole("tab", { name: "Chunks" }).click();
		await expect(page.getByRole("article")).toHaveCount(3);
		await startEditing(page);
		await checkPage(page, "portal-chunks-editing");

		// Edit chunk 1.
		const text1 = card(page, 1).getByLabel("Text of chunk 1");
		await text1.fill(
			"Home collection covers Kothrud, Baner, Aundh, Hadapsar and Viman Nagar, 6:30 am to 11 am, Monday to Saturday.",
		);
		await card(page, 1).getByRole("button", { name: "Save text" }).click();
		await expect(page.getByText("Chunk 1 saved")).toBeVisible();

		// Split chunk 2 at the sentence break.
		await card(page, 2).getByRole("button", { name: "Split" }).click();
		const split = page.getByRole("dialog", { name: "Split chunk 2" });
		await expect(split.getByRole("region", { name: "Second part" })).toContainText("Fasting samples");
		await expectNoAxeViolations(page, "split dialog");
		await split.getByRole("button", { name: "Split chunk" }).click();
		await expect(page.getByText("Chunk 2 split in two")).toBeVisible();
		await expect(page.getByRole("article")).toHaveCount(4);

		// Approve every chunk.
		for (let n = 1; n <= 4; n++) {
			await card(page, n).getByRole("button", { name: "Approve" }).click();
			await expect(card(page, n).getByText("Approved", { exact: true })).toBeVisible();
		}
		await expect(page.getByTestId("chunk-counts")).toHaveText("4 chunks · 4 approved");

		const review = page.getByRole("region", { name: "Review" });
		await review.getByRole("button", { name: "Submit for review" }).click();
		await expect(page.getByText("Submitted for review")).toBeVisible();
		await review.getByRole("button", { name: "Approve" }).click();
		const approve = page.getByRole("alertdialog", { name: "Approve “Home collection policy”" });
		await approve.getByLabel("Comment (optional)").fill("Timings checked with the Baner centre.");
		await approve.getByRole("button", { name: "Approve" }).click();
		await expect(page.getByText("Workspace approved")).toBeVisible();
		await review.getByRole("button", { name: "Ingest" }).click();
		await expect(page.getByText("Ingest started: 4 chunks")).toBeVisible();
		await expect(review.getByRole("link", { name: "View the job" })).toBeVisible();
		await expect(page.getByTestId("lock-bar")).toHaveAttribute("data-state", "closed");
		await checkPage(page, "portal-ingested");
		for (const b of state.bodies) expect(b.body, b.path).not.toHaveProperty("user");

		await page.goto(`/documents?kb=${PATIENT}`);
		await expect(main(page).getByRole("link", { name: "Home collection policy.md" })).toBeVisible({
			timeout: 10_000,
		});
	});

	test("members edit chunks but can't delete a workspace; owners delete a draft", async ({ page }, info) => {
		const role = roleOf(info);
		const id = seedDraft(state, PATIENT, "Report delivery FAQ", "priya@shree-labs.in", [
			"Reports are ready the same day by 7 pm, on WhatsApp and e-mail.",
			"Lost your report? Call +91 98220 12345 with your bill number.",
		]);
		await logIn(page, role);
		await page.goto(`/portal/${id}?kb=${PATIENT}&panel=chunks`);
		await expect(page.getByRole("heading", { level: 1, name: "Report delivery FAQ" })).toBeVisible();
		await startEditing(page);
		await card(page, 2)
			.getByLabel("Text of chunk 2")
			.fill("Lost your report? Call +91 98220 12345 with your bill number, or reply on WhatsApp.");
		await card(page, 2).getByRole("button", { name: "Save text" }).click();
		await expect(page.getByText("Chunk 2 saved")).toBeVisible();
		await card(page, 2).getByRole("button", { name: "Move chunk 2 up" }).click();
		await expect(page.getByText("Chunk moved to position 1")).toBeVisible();
		await expect(card(page, 1).getByLabel("Text of chunk 1")).toHaveValue(/or reply on WhatsApp/);

		const del = page.getByRole("button", { name: "Delete workspace" });
		if (role === "member") {
			await expect(del).toHaveAttribute("aria-disabled", "true");
			// aria-disabled (never silently disabled): the click still reaches the gate and shows the toast.
			await del.click({ force: true });
			await expect(page.getByText("Only owners can delete portal workspaces").first()).toBeVisible();
			await checkPage(page, "portal-member");
			expect(state.workspaces).toHaveLength(1);
			return;
		}
		await del.click();
		const confirm = page.getByRole("alertdialog", { name: "Delete “Report delivery FAQ”?" });
		await confirm.getByLabel(/Type/).fill("Report delivery FAQ");
		await expectNoAxeViolations(page, "delete workspace dialog");
		await confirm.getByRole("button", { name: "Delete workspace" }).click();
		await expect(page).toHaveURL(new RegExp(`/portal\\?kb=${PATIENT}$`));
		await expect(page.getByText("Workspace “Report delivery FAQ” deleted")).toBeVisible();
		expect(state.workspaces).toHaveLength(0);
	});

	test("review an ingested document → edit → publish → new active version; a second review → review_exists", async ({
		page,
	}, info) => {
		test.setTimeout(90_000);
		await logIn(page, roleOf(info));
		const doc = world.docs.docs.find((d) => d.file_name === "home-collection.pdf");
		expect(doc).toBeTruthy();
		await page.goto(`/documents/${doc?.id}?kb=${PATIENT}`);
		await expect(page.getByRole("heading", { level: 1, name: "home-collection.pdf" })).toBeVisible();
		await page.getByRole("button", { name: "Review in Content Portal" }).click();
		await expect(page.getByRole("heading", { level: 1, name: "Review: home-collection.pdf" })).toBeVisible();
		await expect(page.getByTestId("review-counts")).toHaveText("3 chunks · 3 kept · 0 rejected · 0 changed");
		const reviewUrl = page.url();

		// A second review of the same version is refused, with a link to the open one.
		await page.goto(`/documents/${doc?.id}?kb=${PATIENT}`);
		await page.getByRole("button", { name: "Review in Content Portal" }).click();
		await expect(page.getByText("A review of this version is already open.")).toBeVisible();
		await page.getByRole("button", { name: "Open that review" }).click();
		await expect(page).toHaveURL(reviewUrl);

		await startEditing(page);
		// Chunk 1 is the parent of 2 and 3: its text is rebuilt from them, so it can't be edited.
		await expect(card(page, 1).getByText(/^This is a parent chunk/)).toBeVisible();
		await expect(card(page, 1).getByRole("button", { name: "Split" })).toHaveCount(0);
		await expect(card(page, 1).getByLabel("Text of chunk 1")).toHaveAttribute("readonly", "");
		await card(page, 2)
			.getByLabel("Text of chunk 2")
			.fill(
				"Slots 6:30 am to 11 am, Monday to Saturday; Sunday 7 am to 10 am in Kothrud only. Book on +91 98220 12345.",
			);
		await card(page, 2).getByRole("button", { name: "Save text" }).click();
		await expect(page.getByText("Chunk 2 saved")).toBeVisible();
		await expect(page.getByTestId("review-counts")).toHaveText("3 chunks · 3 kept · 0 rejected · 1 changed");
		await checkPage(page, "portal-post-ingest");

		await page.getByRole("button", { name: "Publish as new version" }).click();
		const summary = page.getByTestId("post-ingest-summary");
		await expect(summary.getByRole("heading", { name: "Published as a new version" })).toBeVisible();
		await expect(summary).toContainText("Edited, split or merged");
		await checkPage(page, "portal-published");
		await summary.getByRole("link", { name: "Open document" }).click();
		const versions = page.getByRole("table", { name: "Versions of this document" });
		await expect(versions.locator("tbody tr")).toHaveCount(2);
		await expect(versions.getByText("Active", { exact: true })).toHaveCount(1);
		expect(doc?.versions.at(-1)?.status).toBe("active");
	});

	test("AI chat proposes a change and Apply runs it; the audit trail shows it", async ({ page }, info) => {
		const id = seedDraft(state, PATIENT, "Sample transport", "priya@shree-labs.in", [
			"Samples reach the lab within 2 hours in cold boxes.",
			"Timings: 7 to 11.",
		]);
		await logIn(page, roleOf(info));
		await page.goto(`/portal/${id}?kb=${PATIENT}&panel=chat`);
		await page.getByLabel("Message to the AI").fill("Spell out the short timings chunk");
		await page.getByRole("button", { name: "Send" }).click();
		const proposal = page.getByRole("region", { name: "Proposed change: Rewrite a chunk" });
		await expect(proposal).toBeVisible();
		await expect(proposal.getByRole("button", { name: "Apply" })).toBeDisabled();
		await startEditing(page);
		await proposal.getByRole("button", { name: "Apply" }).click();
		await expect(proposal.getByText("Applied: Chunk rewritten")).toBeVisible();
		await checkPage(page, "portal-chat");
		await page.getByRole("tab", { name: "Audit trail" }).click();
		const audit = page.getByRole("table", { name: "Audit trail" });
		await expect(audit.getByText("Rewritten by AI")).toBeVisible();
		await expect(audit.getByText("Timings: 7 to 11. (Monday to Saturday.)")).toBeVisible();
		await checkPage(page, "portal-audit");
	});

	test("routing rules: add a file-type rule and see it listed", async ({ page }, info) => {
		await logIn(page, roleOf(info));
		await page.goto(`/portal?kb=${PATIENT}&tab=rules`);
		await expect(page.getByTestId("rules-empty")).toBeVisible();
		await page.getByRole("button", { name: "Add rule" }).click();
		const dlg = page.getByRole("dialog", { name: "Add a routing rule" });
		await dlg.getByLabel("File types").fill("application/pdf, text/html");
		await dlg.getByLabel("Priority").fill("10");
		await expectNoAxeViolations(page, "rule dialog");
		await dlg.getByRole("button", { name: "Save rule" }).click();
		const table = page.getByRole("table", { name: "Routing rules" });
		await expect(table.getByText("Type is application/pdf, text/html")).toBeVisible();
		expect(state.rules[0]).toMatchObject({
			rule_type: "mime_type",
			condition: { mime_types: ["application/pdf", "text/html"] },
			priority: 10,
		});
		await checkPage(page, "portal-rules");
	});
});

test.describe("content portal off", () => {
	test.beforeEach(async ({ context }) => {
		world = createWorld();
		await installMockEngine(context, world);
	});

	test("no nav item, and /portal says it is switched off", async ({ page }, info) => {
		await logIn(page, roleOf(info));
		if (!isPhone(page)) {
			await expect(
				page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Content Portal" }),
			).toHaveCount(0);
		}
		await page.goto(`/portal?kb=${PATIENT}`);
		await expect(page.getByTestId("portal-off")).toContainText("RAG_ENABLE_CONTENT_PORTAL");
		await checkPage(page, "portal-off");
	});
});

// The same journeys against the running stack need RAG_ENABLE_CONTENT_PORTAL=true, an LLM for the
// AI steps, a Celery worker and the accounts fixtures: pending until the e2e stack covers them.
test.describe("content portal against the running stack", () => {
	test.fixme(true, "real-stack e2e pending: needs the portal on, an LLM and a worker");
	test("owner prepares, reviews and ingests; member gating; post-ingest review publish", async () => {});
});
