import { expect, type Page, test } from "@playwright/test";
import {
	type AdminState,
	createAdminState,
	ECHO_URL,
	installAdminMock,
	mirrorLifecycleAudit,
	OPENROUTER_TEST_KEY,
} from "./fixtures/mock-admin";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * D10 on the mock engine (e2e/fixtures/mock-admin.ts on top of mock-engine.ts) across the README
 * matrix: sign-up & branding reaching the shell and the public pages, email (dev log provider),
 * audit log, migrations after an embedding change, webhooks to an echo endpoint, models with
 * OpenRouter discovery and engine info. Tenant projects: migrations read-only, models visible,
 * admin pages not available. The same journeys against the running stack are pending below.
 */

const PATIENT = "7b221003-0000-4000-8000-000000000001";
let world: World;
let state: AdminState;

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

/**
 * The toast of one sign-up mode save. A second save's toast can appear while the first is still
 * on screen, so the title alone matches two toasts; the description names the mode just saved.
 */
const modeSavedToast = (page: Page, mode: string) =>
	page
		.locator("[data-sonner-toast]")
		.filter({ hasText: "Sign-up mode saved" })
		.filter({ hasText: `New sign-ups: ${mode}.` });

async function openSidebar(page: Page) {
	if (isPhone(page)) await page.getByRole("button", { name: "Open menu" }).click();
}

test.beforeEach(async ({ context, page }) => {
	world = createWorld();
	await installMockEngine(context, world);
	state = createAdminState(world);
	await installAdminMock(context, world, state);
	await page.emulateMedia({ reducedMotion: "reduce" });
});

test.describe("super admin", () => {
	test.beforeEach(({ browserName: _b }, info) => {
		test.skip(roleOf(info) !== "superadmin", "super admin pages");
	});

	test("branding reaches the shell and the public pages; Closed shows Talk to us; back to Approval", async ({
		page,
		browser,
	}) => {
		test.setTimeout(90_000);
		// A fresh install: engine defaults.
		Object.assign(world.branding, {
			product_name: "cob-rag-engine",
			primary_color: "#2f8cff",
			support_email: null,
			contact_url: null,
		});
		await logIn(page, "superadmin");
		await page.goto("/admin/branding");
		await expect(page.getByRole("heading", { level: 1, name: "Sign-up & branding" })).toBeVisible();
		await page.getByLabel("Product name").fill("Sahayak AI");
		await page.getByLabel("Primary colour", { exact: true }).fill("#0b7a5f");
		await page.getByLabel("Support email").fill("support@sahayak.ai");
		await page.getByLabel("Contact link").fill("https://wa.me/919822012345");
		await expect(page.getByTestId("preview-name")).toHaveText("Sahayak AI");
		await expect(page.getByTestId("contrast")).toContainText("readable");
		await page.getByRole("button", { name: "Save changes" }).click();
		await expect(page.getByText("Branding saved")).toBeVisible();
		await expect(page).toHaveTitle("Sign-up & branding · Sahayak AI");
		await openSidebar(page);
		await expect(page.getByRole("link", { name: "Sahayak AI" }).first()).toBeVisible();
		if (isPhone(page)) await page.keyboard.press("Escape");
		expect(state.audit.at(-1)).toMatchObject({ action: "settings.updated" });
		await checkPage(page, "branding");

		await page
			.getByRole("radiogroup", { name: "Sign-up mode" })
			.getByRole("radio", { name: /^Closed/ })
			.click();
		await expect(page.getByTestId("preview-contact")).toBeVisible();
		await checkPage(page, "branding-closed-preview");
		await page.getByRole("button", { name: "Save", exact: true }).click();
		await expect(modeSavedToast(page, "Closed")).toBeVisible();

		// Another browser (signed out): the login page and the closed sign-up page.
		const other = await browser.newContext();
		await installMockEngine(other, world);
		await installAdminMock(other, world, state);
		const visitor = await other.newPage();
		await visitor.goto("/login");
		await expect(visitor.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
		await expect(visitor.getByTestId("powered-by")).toContainText("cob-rag-engine by Callobuzz");
		await visitor.goto("/signup");
		await expect(visitor.getByRole("heading", { name: "Talk to us to get started" })).toBeVisible();
		await expect(visitor.getByTestId("powered-by")).toBeVisible();
		await other.close();

		await page
			.getByRole("radiogroup", { name: "Sign-up mode" })
			.getByRole("radio", { name: /^Approval required/ })
			.click();
		await page.getByRole("button", { name: "Save", exact: true }).click();
		await expect(modeSavedToast(page, "Approval required")).toBeVisible();
		expect(state.signupMode).toBe("approval");
	});

	test("email: development log provider and a test send", async ({ page }) => {
		await logIn(page, "superadmin");
		await page.goto("/admin/email");
		await expect(main(page).getByTestId("smtp-provider")).toHaveText("Development log");
		await expect(main(page).getByText(/printed to the server log/)).toBeVisible();
		await expect(main(page).getByText("RAG_SMTP_PASSWORD")).toBeVisible();
		await checkPage(page, "email");
		await expect(page.getByLabel("Send the test to")).toHaveValue("sv@punedigital.in");
		await page.getByRole("button", { name: "Send test email" }).click();
		await expect(page.getByText("Test email written to the server log")).toBeVisible();
		expect(state.smtpTests).toEqual(["sv@punedigital.in"]);
	});

	test("audit log: plain labels, D7's customer history link, details", async ({ page }) => {
		await logIn(page, "superadmin");
		// D7: activate a pending sign-up → an audit row (written with the change, like the engine).
		await page.goto("/admin/customers?status=pending");
		await page.getByRole("button", { name: "Activate Kothrud Physio Clinic" }).click();
		const dialog = page.getByRole("dialog", { name: "Activate Kothrud Physio Clinic" });
		await dialog.getByRole("button", { name: "Activate" }).click();
		await expect(page.getByText("Kothrud Physio Clinic is active")).toBeVisible();
		mirrorLifecycleAudit(world, state);
		await page.goto("/admin/audit");
		const table = page.getByRole("table", { name: "Audit log" });
		const kothrud = table.getByRole("row").filter({ hasText: "Kothrud Physio Clinic" });
		await expect(kothrud).toContainText("Activated a customer");
		await expect(table.getByText("Suspended a customer")).toBeVisible();
		await checkPage(page, "audit");
		const mehta = world.tenants.find((t) => t.name === "Mehta Opticals")?.id ?? "";
		await page.goto(`/admin/audit?tenant_id=${mehta}&action=tenant.`);
		await expect(table.getByText("Suspended a customer")).toBeVisible();
		await expect(table.getByText("Activated a customer")).toHaveCount(0);
		await table.getByRole("button", { name: "Show details" }).click();
		await expect(page.getByTestId("audit-details")).toContainText("Unpaid for 2 months");
		await checkPage(page, "audit-filtered");
	});

	test("migrations: an embedding change (D3) → pending → approve → progress → completed", async ({ page }) => {
		test.setTimeout(90_000);
		await logIn(page, "superadmin");
		await page.goto(`/kbs/${PATIENT}?tab=embedding`);
		await page.getByLabel("Model", { exact: true }).selectOption("tei|BAAI/bge-base-en-v1.5");
		await page.getByRole("button", { name: "Change model" }).click();
		await page.getByRole("button", { name: "Open migration" }).click();
		const drawer = page.getByRole("dialog", { name: "Migration of Patient help" });
		await expect(drawer.getByText("Waiting for approval")).toBeVisible();
		await checkPage(page, "migration-pending");
		await drawer.getByRole("button", { name: "Approve" }).click();
		await expect(page.getByText("Migration approved")).toBeVisible();
		await expect(drawer.getByRole("progressbar", { name: "Passages" })).toBeVisible({ timeout: 15_000 });
		await expect(drawer.getByText("Completed")).toBeVisible({ timeout: 20_000 });
		await expect(drawer.getByRole("button", { name: "Roll back" })).toBeEnabled();
		await page.keyboard.press("Escape");
		const table = page.getByRole("table", { name: "Embedding migrations" });
		await expect(table.getByText("Completed")).toBeVisible();
		await checkPage(page, "migrations");
		expect(world.kbs.items.find((k) => k.id === PATIENT)?.applied_embedding).toMatchObject({
			model: "BAAI/bge-base-en-v1.5",
		});
	});

	test("webhooks: create with a browser-made secret, test to the echo endpoint, pause, delete", async ({
		page,
		context,
	}) => {
		await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: "http://127.0.0.1:5173" });
		await logIn(page, "superadmin");
		await page.goto("/admin/webhooks");
		await expect(page.getByRole("heading", { name: "No webhooks yet" })).toBeVisible();
		await checkPage(page, "webhooks-empty");
		await main(page).getByRole("button", { name: "New webhook" }).first().click();
		const dialog = page.getByRole("dialog", { name: "New webhook" });
		const secret = await dialog.getByLabel("Signing secret", { exact: true }).inputValue();
		expect(secret).toMatch(/^[A-Za-z0-9_-]{43}$/);
		await dialog.getByRole("button", { name: "Copy signing secret" }).click();
		await expect(dialog.getByText("Copied").first()).toBeVisible();
		await dialog.getByLabel("Address").fill(ECHO_URL);
		await dialog.getByRole("checkbox", { name: /Evaluation score dropped/ }).click();
		await expectNoAxeViolations(page, "webhook dialog");
		await fullShot(page, screenshotPath(test.info(), "webhook-new"));
		await dialog.getByRole("button", { name: "Create webhook" }).click();
		await expect(page.getByText("Webhook created")).toBeVisible();
		expect(state.hooks[0]?.secret).toBe(secret);
		const table = page.getByRole("table", { name: "Webhooks" });
		await table.getByRole("button", { name: `Send a test to ${ECHO_URL}` }).click();
		await expect(page.getByText("Test sent")).toBeVisible();
		expect(state.deliveries).toHaveLength(1);
		expect(state.deliveries[0]?.valid).toBe(true);
		await table.getByRole("switch", { name: `Send to ${ECHO_URL}` }).click();
		await expect(page.getByText("Webhook paused")).toBeVisible();
		expect(state.hooks[0]?.enabled).toBe(false);
		await checkPage(page, "webhooks");
		await table.getByRole("button", { name: `Delete the webhook to ${ECHO_URL}` }).click();
		await page
			.getByRole("alertdialog", { name: "Delete this webhook?" })
			.getByRole("button", { name: "Delete" })
			.click();
		await expect(page.getByRole("heading", { name: "No webhooks yet" })).toBeVisible();
	});

	test("engine info, health and engine-wide retrieval defaults", async ({ page }) => {
		await logIn(page, "superadmin");
		await page.goto("/admin/engine");
		await expect(main(page).getByText("0.1.0")).toBeVisible();
		await expect(main(page).getByText(/RAG_PUBLIC_URL isn't set/)).toBeVisible();
		await expect(main(page).getByTestId("engine-service-qdrant")).toContainText("OK");
		await page.getByRole("button", { name: "Check again" }).click();
		await checkPage(page, "engine");
		const half = page.getByLabel("Recency half-life (days)");
		await half.fill("14");
		await page.getByRole("button", { name: "Save defaults" }).click();
		await expect(page.getByText("Retrieval defaults saved")).toBeVisible();
		expect(state.retrievalPuts).toEqual([{ temporal_decay_half_life_days: 14 }]);
		await expect(page.getByTestId("retrieval-source")).toHaveText("Changed here");
	});
});

test("models: catalog in ₹, presets, embeddings and OpenRouter discovery (every role)", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await page.goto("/models");
	await expect(page.getByRole("heading", { level: 1, name: "Models" })).toBeVisible();
	await expect(page.getByRole("table", { name: "Answer models" })).toBeVisible();
	await checkPage(page, "models");
	await page.getByRole("tab", { name: "Presets" }).click();
	await expect(page.getByRole("table", { name: "Retrieval presets" })).toBeVisible();
	await page.getByRole("tab", { name: "OpenRouter" }).click();
	await page.getByLabel("OpenRouter key").fill(OPENROUTER_TEST_KEY);
	await page.getByRole("button", { name: "Find models" }).click();
	await expect(
		page.getByRole("table", { name: "Models on OpenRouter" }).getByText("Sarvam-M", { exact: true }),
	).toBeVisible();
	await expect(page.getByLabel("OpenRouter key")).toHaveValue("");
	expect(state.openRouterKeys).toEqual([OPENROUTER_TEST_KEY]);
	const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
	expect(stored).not.toContain("sk-or");
	await checkPage(page, "models-openrouter");
});

test.describe("tenant view", () => {
	test.beforeEach(({ browserName: _b }, info) => {
		test.skip(roleOf(info) === "superadmin", "tenant view");
	});

	test("migrations are read-only; admin pages are not available", async ({ page }) => {
		const kb = world.kbs.items.find((k) => k.id === PATIENT);
		if (kb) kb.embedding_config = { provider: "tei", model: "BAAI/bge-base-en-v1.5", dimensions: 768 };
		await logIn(page, roleOf(test.info()));
		await page.goto("/migrations");
		await expect(page.getByText("The operator approves migrations. You can watch their progress here.")).toBeVisible();
		const table = page.getByRole("table", { name: "Embedding migrations" });
		await expect(table.getByText("Waiting for approval")).toBeVisible();
		await expect(page.getByRole("button", { name: /Approve all pending/ })).toHaveCount(0);
		await table.getByRole("button", { name: "Open migration of Patient help" }).click();
		const drawer = page.getByRole("dialog", { name: "Migration of Patient help" });
		await expect(drawer.getByText("Waiting for approval")).toBeVisible();
		await expect(drawer.getByRole("button", { name: "Approve" })).toHaveCount(0);
		await checkPage(page, "migrations-tenant");
		for (const path of ["/admin/branding", "/admin/email", "/admin/audit", "/admin/webhooks", "/admin/engine"]) {
			await page.goto(path);
			await expect(page.getByRole("heading", { name: "Not available for your account" }), path).toBeVisible();
			await expect(page.getByTestId("powered-by")).toBeVisible();
		}
	});
});

/** Against the running stack (A2/A7/A8 merged, `docker compose up`): pending. */
test.describe("admin pages on the real engine", () => {
	test.fixme("branding, SMTP test (dev log), audit rows, a real migration and a webhook echo on the running stack", async () => {});
});
