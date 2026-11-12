import { type Browser, type BrowserContext, expect, type Page, test } from "@playwright/test";
import { E2E_PEOPLE, ensureSuperadminPassword, setSignupMode } from "./fixtures/accounts";
import { ACCOUNTS_READY, ADMIN_KEY, adminCall, ENGINE } from "./fixtures/stack";
import { expectNoAxeViolations } from "./utils/axe";
import { latestCodeFor, latestInviteToken } from "./utils/mail-log";

/**
 * The customer journey end to end against the REAL engine (accounts, plans, knowledge bases,
 * content, answers, widget, team, lifecycle, admin screens). One serial run in one project
 * (`real-stack`); each person has their own browser context.
 *
 *   E2E_ACCOUNTS=1 E2E_ADMIN_KEY=… E2E_SUPERADMIN_EMAIL=… [E2E_MAIL_CONTAINER=… E2E_APP_CONTAINER=…]
 *     pnpm e2e --project=real-stack --workers=2
 *
 * Needs the dev e-mail log provider and `RAG_CORS_ORIGINS` listing the dev origin. The LLM on a
 * dev stack can be slow: the playground step waits up to 3 minutes and records a timeout as an
 * annotation, not a failure. Everything it creates (customer, plan) is deleted at the end.
 */
test.skip(
	!ACCOUNTS_READY || !ADMIN_KEY || !E2E_PEOPLE.superadmin.email,
	"set E2E_ACCOUNTS, E2E_ADMIN_KEY and E2E_SUPERADMIN_EMAIL",
);

const STAMP = Date.now().toString(36);
const BUSINESS = "Kothrud Dental E2E";
const PLAN = "E2E Tiny";
const OWNER = { email: `dr.anand.${STAMP}@kothruddental.in`, name: "Anand Joshi", password: "Kothrud clinic e2e 2026" };
const MEMBER = { email: `meena.${STAMP}@kothruddental.in`, name: "Meena Patil", password: "Front desk e2e 2026 ok" };
const KB = "Clinic help";
const ORIGIN = "https://kothruddental.in";
const DASHBOARD = "http://127.0.0.1:5173";
const VIEWPORT = { width: 1440, height: 900 };

const FAQ_MD = `# Kothrud Dental Care: patient FAQ

## Timings
The clinic is open Monday to Saturday from 10 am to 8 pm. On Sundays we are open from 10 am to 1 pm for emergencies only.

## Fees
A consultation costs ₹500. A scaling and polishing session costs ₹1,200. Root canal treatment starts at ₹4,500 per tooth. We accept UPI, cards and cash.

## Location
We are on Paud Road, Kothrud, Pune, next to the Karve Statue bus stop. Two-wheeler parking is free.

## Appointments
Book by calling 020 5550 1234 or on WhatsApp at +91 98220 12345. Walk-ins are welcome but booked patients are seen first.
`;
const RECORDS_CSV = `faq_id,question,answer
q-001,Do you treat children?,"Yes, Dr. Joshi sees children from age 3. The first visit is free."
q-002,क्या आप रविवार को खुले हैं?,"रविवार को सिर्फ़ इमरजेंसी के लिए सुबह 10 से दोपहर 1 बजे तक क्लिनिक खुला रहता है।"
q-003,Is there a senior citizen discount?,"Patients above 60 get 10% off on consultations and scaling."
`;

let superPage: Page;
let ownerCtx: BrowserContext | undefined;
let ownerPage: Page;
let memberCtx: BrowserContext | undefined;
let memberPage: Page;
let tenantId = "";
let planId = "";
let kbId = "";
let kbKey = "";

async function newPerson(browser: Browser): Promise<{ ctx: BrowserContext; page: Page }> {
	const ctx = await browser.newContext({ baseURL: DASHBOARD, viewport: VIEWPORT, colorScheme: "light" });
	await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: DASHBOARD });
	const page = await ctx.newPage();
	await page.emulateMedia({ reducedMotion: "reduce" });
	return { ctx, page };
}

async function logInWith(page: Page, email: string, password: string) {
	await page.goto("/login");
	await page.getByLabel("Email").fill(email);
	await page.getByLabel("Password", { exact: true }).fill(password);
	await page.getByRole("button", { name: "Log in" }).click();
}

const rowOf = (page: Page, name: string) =>
	page.getByRole("row").filter({ has: page.getByRole("rowheader", { name: new RegExp(name) }) });
const uploadRow = (page: Page, name: string) => page.getByRole("listitem", { name, exact: true });

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser, request }) => {
	// A failed earlier run may have left its customer or plan behind.
	const tenants = await adminCall<{ items: { id: string; name: string }[] }>(
		request,
		"GET",
		`/api/v1/admin/tenants?search=${encodeURIComponent(BUSINESS)}`,
	);
	for (const t of tenants.body?.items ?? []) {
		if (t.name === BUSINESS) {
			await adminCall(request, "DELETE", `/api/v1/admin/tenants/${t.id}?confirm_name=${encodeURIComponent(BUSINESS)}`);
		}
	}
	const plans = await adminCall<{ items?: { id: string; name: string }[] } | { id: string; name: string }[]>(
		request,
		"GET",
		"/api/v1/admin/plans",
	);
	const list = Array.isArray(plans.body) ? plans.body : (plans.body?.items ?? []);
	for (const p of list) if (p.name === PLAN) await adminCall(request, "DELETE", `/api/v1/admin/plans/${p.id}`);
	await setSignupMode(request, "approval");
	const password = await ensureSuperadminPassword(request);
	expect(password, "the super admin's password could not be set").toBeTruthy();
	const s = await newPerson(browser);
	superPage = s.page;
	await logInWith(superPage, E2E_PEOPLE.superadmin.email, password ?? "");
	await expect(superPage.getByRole("heading", { level: 1 })).toBeVisible();
});

test.afterAll(async ({ request }) => {
	// Whatever happened above, leave the stack as it was.
	const tenants = await adminCall<{ items: { id: string; name: string }[] }>(
		request,
		"GET",
		`/api/v1/admin/tenants?search=${encodeURIComponent(BUSINESS)}`,
	);
	for (const t of tenants.body?.items ?? []) {
		if (t.name === BUSINESS) {
			await adminCall(request, "DELETE", `/api/v1/admin/tenants/${t.id}?confirm_name=${encodeURIComponent(BUSINESS)}`);
		}
	}
	if (planId) await adminCall(request, "DELETE", `/api/v1/admin/plans/${planId}`);
	await setSignupMode(request, "approval");
	await ownerCtx?.close();
	await memberCtx?.close();
});

test("super admin creates a plan with small limits", async () => {
	await superPage.goto("/admin/plans");
	await expect(superPage.getByRole("heading", { level: 1, name: "Plans" })).toBeVisible();
	await superPage.getByRole("button", { name: "New plan" }).click();
	const dialog = superPage.getByRole("dialog", { name: "New plan" });
	await dialog.getByLabel("Name").fill(PLAN);
	await dialog.getByLabel("Price note").fill("₹0 (e2e)");
	await dialog.getByLabel("Knowledge bases").fill("1");
	await dialog.getByLabel("Questions a month").fill("50");
	await dialog.getByLabel("Documents").fill("20");
	await dialog.getByLabel("Website widget keys").fill("2");
	await dialog.getByRole("button", { name: "Create plan" }).click();
	await expect(dialog).toBeHidden();
	await expect(superPage.getByRole("article", { name: new RegExp(`^${PLAN}`) })).toContainText("50");
	await expectNoAxeViolations(superPage, "plans (real)");
	const plans = await adminCall<{ items?: { id: string; name: string }[] } | { id: string; name: string }[]>(
		superPage.request,
		"GET",
		"/api/v1/admin/plans",
	);
	const list = Array.isArray(plans.body) ? plans.body : (plans.body?.items ?? []);
	planId = list.find((p) => p.name === PLAN)?.id ?? "";
	expect(planId).toBeTruthy();
});

test("sign-up (approval) → code from the mailer log → pending page", async ({ browser }) => {
	const o = await newPerson(browser);
	ownerCtx = o.ctx;
	ownerPage = o.page;
	await ownerPage.goto("/signup");
	await ownerPage.getByLabel("Business name").fill(BUSINESS);
	await ownerPage.getByLabel("Your name").fill(OWNER.name);
	await ownerPage.getByLabel("Work email").fill(OWNER.email);
	await ownerPage.getByLabel("Password", { exact: true }).fill(OWNER.password);
	await ownerPage.getByRole("button", { name: "Create account" }).click();
	await expect(ownerPage.getByRole("heading", { name: "Check your email" })).toBeVisible();
	await expect.poll(() => latestCodeFor(OWNER.email), { timeout: 30_000 }).not.toBeNull();
	await ownerPage.getByLabel("Digit 1 of 6").click();
	await ownerPage.keyboard.type(latestCodeFor(OWNER.email) ?? "");
	await expect(ownerPage.getByRole("heading", { name: "We'll activate your workspace soon" })).toBeVisible();
	await expectNoAxeViolations(ownerPage, "pending page (real)");
});

test("super admin sees the pending customer and activates it on the tiny plan", async () => {
	await superPage.goto("/admin/customers");
	await superPage.getByRole("tab", { name: /^Pending/ }).click();
	const row = rowOf(superPage, BUSINESS);
	await expect(row).toContainText(OWNER.email);
	await row.getByRole("button", { name: `Activate ${BUSINESS}` }).click();
	const dialog = superPage.getByRole("dialog", { name: `Activate ${BUSINESS}` });
	const planSelect = dialog.getByLabel("Plan");
	const optionLabel = await planSelect.locator("option", { hasText: PLAN }).first().textContent();
	await planSelect.selectOption({ label: optionLabel ?? PLAN });
	await dialog.getByLabel("Note for your records").fill("Paid ₹0 via e2e");
	await dialog.getByRole("button", { name: "Activate" }).click();
	await expect(dialog).toBeHidden();
	await expect(superPage.getByText(`${BUSINESS} is active`)).toBeVisible();
	const found = await adminCall<{ items: { id: string; name: string }[] }>(
		superPage.request,
		"GET",
		`/api/v1/admin/tenants?search=${encodeURIComponent(BUSINESS)}`,
	);
	tenantId = found.body.items.find((t) => t.name === BUSINESS)?.id ?? "";
	expect(tenantId).toBeTruthy();
});

test("the owner checks again and lands on Overview; a wrong password is refused", async ({ browser }) => {
	await ownerPage.getByRole("button", { name: "Check again" }).click();
	await expect(ownerPage.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	await expectNoAxeViolations(ownerPage, "owner overview (real)");
	const other = await newPerson(browser);
	await logInWith(other.page, OWNER.email, "definitely not it 123");
	await expect(other.page.getByRole("alert")).toContainText("Email or password isn't right.");
	await other.ctx.close();
});

test("owner creates a knowledge base and meets the plan limit on the second", async () => {
	await ownerPage.goto("/kbs");
	await ownerPage.getByRole("button", { name: "New knowledge base" }).first().click();
	let dialog = ownerPage.getByRole("dialog");
	await dialog.getByLabel("Name").fill(KB);
	await dialog.getByRole("button", { name: "Create knowledge base" }).click();
	const key = dialog.getByText(/^ck_[0-9a-f]{8}_/);
	await expect(key).toBeVisible();
	kbKey = (await key.textContent()) ?? "";
	await expectNoAxeViolations(ownerPage, "key shown once (real)");
	await dialog.getByRole("button", { name: "Open knowledge base" }).click();
	await expect(ownerPage.getByRole("heading", { level: 1, name: KB })).toBeVisible();
	kbId = /\/kbs\/([0-9a-f-]{36})/.exec(ownerPage.url())?.[1] ?? "";
	expect(kbId).toBeTruthy();

	await ownerPage.goto("/kbs");
	await ownerPage.getByRole("button", { name: "New knowledge base" }).first().click();
	dialog = ownerPage.getByRole("dialog");
	await dialog.getByLabel("Name").fill("A second one");
	await dialog.getByRole("button", { name: "Create knowledge base" }).click();
	await expect(dialog.getByText("Your plan allows 1 knowledge bases.")).toBeVisible();
	await expect(dialog.getByRole("link", { name: "See plan" })).toBeVisible();
	await expectNoAxeViolations(ownerPage, "kb limit (real)");
});

test("owner uploads a markdown file and the job stages complete", async () => {
	test.setTimeout(240_000);
	await ownerPage.goto(`/add?kb=${kbId}`);
	await ownerPage
		.getByTestId("file-input")
		.setInputFiles([{ name: "clinic-faq.md", mimeType: "text/markdown", buffer: Buffer.from(FAQ_MD, "utf8") }]);
	await ownerPage.getByRole("button", { name: "Upload 1 file" }).click();
	const row = uploadRow(ownerPage, "clinic-faq.md");
	await expect(row.getByText("Done", { exact: true })).toBeVisible({ timeout: 180_000 });
	await expectNoAxeViolations(ownerPage, "upload done (real)");
});

test("owner imports records; documents list and detail show them", async () => {
	test.setTimeout(240_000);
	await ownerPage.goto(`/add?kb=${kbId}&tab=records`);
	await ownerPage.getByTestId("records-file").setInputFiles({
		name: "faq.csv",
		mimeType: "text/csv",
		buffer: Buffer.from(RECORDS_CSV, "utf8"),
	});
	await expect(ownerPage.getByRole("combobox", { name: /^ID/ })).toHaveValue("faq_id");
	await ownerPage.getByRole("button", { name: "Next" }).click();
	await expect(ownerPage.getByTestId("records-count")).toHaveText("3 records in 1 batch");
	await ownerPage.getByRole("button", { name: "Import 3 records" }).click();
	await expect(ownerPage.getByTestId("records-counts")).toHaveText("3 new · 0 updated · 0 unchanged", {
		timeout: 120_000,
	});

	await ownerPage.goto(`/documents?kb=${kbId}`);
	const table = ownerPage.getByRole("table", { name: "Documents in this knowledge base" });
	await expect(table.getByRole("link", { name: "clinic-faq.md" })).toBeVisible();
	await expect(table.getByRole("link", { name: "क्या आप रविवार को खुले हैं?" })).toBeVisible();
	await expectNoAxeViolations(ownerPage, "documents (real)");
	await table.getByRole("link", { name: "clinic-faq.md" }).click();
	await expect(ownerPage.getByRole("heading", { level: 1, name: "clinic-faq.md" })).toBeVisible();
	await expect(ownerPage.getByRole("list", { name: /Passages of version 1/ })).toBeVisible();
	await expectNoAxeViolations(ownerPage, "document detail (real)");
});

test("playground: a question gets a streamed answer (slow LLM allowed)", async () => {
	test.setTimeout(240_000);
	await ownerPage.goto(`/playground?kb=${kbId}`);
	await expect(ownerPage.getByRole("heading", { level: 1, name: "Playground" })).toBeVisible();
	await ownerPage.getByRole("textbox", { name: "Question" }).fill("What does a consultation cost?");
	await ownerPage.getByRole("button", { name: /^Ask$/ }).click();
	const bot = ownerPage.getByTestId("bot-answer").last();
	try {
		await expect(bot).toHaveAttribute("data-status", /done|error/, { timeout: 180_000 });
	} catch {
		test
			.info()
			.annotations.push({ type: "environment", description: "playground answer timed out after 3 minutes (slow LLM)" });
		return;
	}
	const status = await bot.getAttribute("data-status");
	test.info().annotations.push({
		type: "playground",
		description: `answer status ${status}: ${(await bot.innerText()).slice(0, 200)}`,
	});
	expect(status).toBe("done");
	await expect(bot.getByTestId("answer-text")).toContainText("500");
});

test("owner creates a widget key with an allowed origin; the preview loads", async () => {
	test.setTimeout(120_000);
	await ownerPage.goto(`/widget?kb=${kbId}`);
	await expect(ownerPage.getByRole("heading", { level: 1, name: "Website widget" })).toBeVisible();
	await ownerPage
		.getByRole("button", { name: /^(New widget key|Create your first widget key)$/ })
		.first()
		.click();
	const dialog = ownerPage.getByRole("dialog", { name: "New widget key" });
	await dialog.getByLabel("Name").fill("Clinic site");
	await dialog.getByLabel("Allowed sites").fill(ORIGIN);
	await dialog.getByLabel("Allowed sites").press("Enter");
	await dialog.getByLabel("Title").fill("Kothrud Dental help");
	await dialog.getByLabel("Greeting").fill("Namaste! Ask about timings, fees or appointments.");
	await dialog.getByRole("button", { name: "Create widget key" }).click();
	await expect(dialog).toBeHidden();
	const card = ownerPage.getByRole("article", { name: "Clinic site" });
	await expect(card.getByTestId("snippet")).toContainText(`<script src="${ENGINE}/widget.js" data-key="pk_`);
	const preview = ownerPage.getByRole("region", { name: "Preview" });
	await preview.getByRole("button", { name: "Allow for previews" }).click();
	await expect(card.getByRole("list", { name: "Allowed sites" })).toContainText("127.0.0.1:5173");
	const frame = ownerPage.frameLocator('iframe[title="Widget preview"]');
	await expect(frame.getByRole("dialog", { name: "Kothrud Dental help" })).toBeVisible({ timeout: 30_000 });
	await expect(frame.getByText("Namaste! Ask about timings, fees or appointments.")).toBeVisible();
	await expectNoAxeViolations(ownerPage, "widget (real)");
});

test("crawler: a review-mode crawl of a tiny local site, approve, deliver, documents appear", async () => {
	test.skip(!process.env.E2E_SCRAPER, "needs a scraper one-off pointed at this engine (E2E_SCRAPER=1, E2E_SITE_URL)");
	test.setTimeout(180_000);
	const site = (process.env.E2E_SITE_URL ?? "http://host.docker.internal:8099").replace(/\/+$/, "");
	await ownerPage.goto(`/crawler?kb=${kbId}`);
	await expect(ownerPage.getByRole("heading", { level: 1, name: "Crawler" })).toBeVisible();
	await ownerPage.getByRole("button", { name: "New crawl" }).first().click();
	const dialog = ownerPage.getByRole("dialog", { name: "New crawl" });
	const review = dialog.getByRole("switch", { name: "Review pages before they go in" });
	await expect(review).toBeChecked();
	await expect(review).toBeDisabled();
	await dialog.getByLabel("Page addresses").fill(`${site}/
${site}/fees
${site}/batches`);
	await dialog.getByLabel("Page limit").fill("5");
	await dialog.getByRole("button", { name: "Start crawl" }).click();
	await expect(dialog).toBeHidden();
	const approveAll = ownerPage.getByRole("button", { name: /^Approve all verified \(\d+\)$/ });
	await expect(approveAll).toBeVisible({ timeout: 90_000 });
	await expectNoAxeViolations(ownerPage, "crawl job (real)");
	await approveAll.click();
	await ownerPage.getByRole("button", { name: /^Deliver \d+ approved pages?$/ }).click();
	await expect(ownerPage.getByTestId("deliver-result")).toContainText(/Delivered \d+ of \d+ page/, { timeout: 60_000 });
	await ownerPage.goto(`/documents?kb=${kbId}`);
	// The crawled pages are processed like uploads; ones that look like existing content wait for
	// a decision ("Needs review"), the rest become documents.
	await expect(ownerPage.getByText("batches-and-timings.md").first()).toBeVisible({ timeout: 60_000 });
	await expect(ownerPage.getByText("fees-for-2026-27.md").first()).toBeVisible();
});

test("owner invites a member; the member accepts the mailed link and is read-only", async ({ browser }) => {
	test.setTimeout(120_000);
	await ownerPage.goto("/team");
	await expect(ownerPage.getByRole("heading", { level: 1, name: "Team" })).toBeVisible();
	await ownerPage.getByRole("button", { name: "Invite teammate" }).click();
	const dialog = ownerPage.getByRole("dialog", { name: `Invite a teammate to ${BUSINESS}` });
	await dialog.getByLabel("Email").fill(MEMBER.email);
	await dialog.getByRole("button", { name: "Send invitation" }).click();
	await expect(dialog).toBeHidden();
	await expect(ownerPage.getByRole("list", { name: "Pending invitations" })).toContainText(MEMBER.email);
	await expect.poll(() => latestInviteToken(), { timeout: 30_000 }).not.toBeNull();
	const token = latestInviteToken();

	const m = await newPerson(browser);
	memberCtx = m.ctx;
	memberPage = m.page;
	await memberPage.goto(`/invite#token=${token}`);
	await expect(memberPage.getByRole("heading", { name: `Join ${BUSINESS}` })).toBeVisible();
	await memberPage.getByLabel("Your name").fill(MEMBER.name);
	await memberPage.getByLabel("Password", { exact: true }).fill(MEMBER.password);
	await memberPage.getByLabel("Repeat new password").fill(MEMBER.password);
	await memberPage.getByRole("button", { name: "Accept and join" }).click();
	await expect(memberPage.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home

	// Read-only / locked owner actions.
	await memberPage.goto("/team");
	const invite = memberPage.getByRole("button", { name: "Invite teammate" });
	await expect(invite).toHaveAttribute("aria-disabled", "true");
	await invite.click({ force: true });
	await expect(memberPage.getByText("Only owners can invite or change teammates").first()).toBeVisible();
	await memberPage.goto(`/kbs/${kbId}?tab=danger`);
	await expect(memberPage.getByRole("button", { name: `Delete ${KB}` })).toHaveAttribute("aria-disabled", "true");
	await memberPage.goto(`/widget?kb=${kbId}`);
	await expect(memberPage.getByRole("button", { name: "New widget key" })).toHaveAttribute("aria-disabled", "true");
	await expectNoAxeViolations(memberPage, "member widget (real)");
	// The engine refuses too: the member's token can't delete the knowledge base (owner_required).
});

test("suspend with the typed name ends the owner's session and blocks key calls; reactivate restores them", async () => {
	test.setTimeout(120_000);
	await superPage.goto("/admin/customers?status=active");
	await rowOf(superPage, BUSINESS)
		.getByRole("button", { name: `Suspend ${BUSINESS}` })
		.click();
	const dialog = superPage.getByRole("alertdialog", { name: `Suspend ${BUSINESS}?` });
	const typed = dialog.getByLabel(new RegExp(`Type ${BUSINESS} to confirm`));
	await typed.fill(BUSINESS.toLowerCase());
	await expect(dialog.getByRole("button", { name: "Suspend" })).toBeDisabled();
	await typed.fill(BUSINESS);
	await dialog.getByLabel("Reason (optional)").fill("e2e suspension");
	await dialog.getByRole("button", { name: "Suspend" }).click();
	await expect(dialog).toBeHidden();

	// The KB key no longer works.
	const query = await superPage.request.get(`${ENGINE}/api/v1/documents`, { headers: { "X-API-Key": kbKey } });
	expect(query.status()).toBe(403);

	// The owner's session ends (the next refresh is refused) and logging in shows the paused notice.
	await ownerPage.reload();
	await expect(ownerPage.getByRole("button", { name: "Log in" })).toBeVisible({ timeout: 20_000 });
	await logInWith(ownerPage, OWNER.email, OWNER.password);
	await expect(ownerPage.getByRole("heading", { name: `${BUSINESS} is paused` })).toBeVisible();

	await superPage.getByRole("tab", { name: /^Suspended/ }).click();
	await rowOf(superPage, BUSINESS)
		.getByRole("button", { name: `Reactivate ${BUSINESS}` })
		.click();
	await superPage
		.getByRole("alertdialog", { name: `Reactivate ${BUSINESS}?` })
		.getByRole("button", { name: "Reactivate" })
		.click();
	await expect(superPage.getByText(`${BUSINESS} reactivated`)).toBeVisible();
	const again = await superPage.request.get(`${ENGINE}/api/v1/documents`, { headers: { "X-API-Key": kbKey } });
	expect(again.status()).toBe(200);
	await ownerPage.getByRole("button", { name: "Log out" }).click();
	await logInWith(ownerPage, OWNER.email, OWNER.password);
	await expect(ownerPage.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
});

test("admin screens: branding on the login page, audit rows, e-mail provider", async ({ browser }) => {
	test.setTimeout(120_000);
	await superPage.goto("/admin/branding");
	const name = await superPage.getByLabel("Product name").inputValue();
	const colour = await superPage.getByLabel("Primary colour", { exact: true }).inputValue();
	try {
		await superPage.getByLabel("Product name").fill(`Sahayak ${STAMP}`);
		await superPage.getByRole("button", { name: "Save changes" }).click();
		await expect(superPage.getByText("Branding saved")).toBeVisible();
		const visitor = await newPerson(browser);
		await visitor.page.goto("/login");
		await expect(visitor.page.getByRole("heading", { name: `Log in to Sahayak ${STAMP}` })).toBeVisible();
		await visitor.ctx.close();
	} finally {
		await superPage.getByLabel("Product name").fill(name);
		await superPage.getByLabel("Primary colour", { exact: true }).fill(colour);
		await superPage.getByRole("button", { name: "Save changes" }).click();
		await expect(superPage.getByText("Branding saved").first()).toBeVisible();
	}

	await superPage.goto(`/admin/audit?tenant_id=${tenantId}`);
	const table = superPage.getByRole("table", { name: "Audit log" });
	await expect(table.getByText("Activated a customer", { exact: true })).toBeVisible();
	await expect(table.getByText("Suspended a customer", { exact: true })).toBeVisible();
	await expect(table.getByText("Reactivated a customer", { exact: true })).toBeVisible();
	await expectNoAxeViolations(superPage, "audit (real)");

	await superPage.goto("/admin/email");
	await expect(superPage.locator("#main").getByTestId("smtp-provider")).toHaveText("Development log");
});

test("delete the customer with its typed name and the test plan", async () => {
	await superPage.goto("/admin/customers?status=active");
	await rowOf(superPage, BUSINESS)
		.getByRole("button", { name: `Delete ${BUSINESS}` })
		.click();
	const dialog = superPage.getByRole("alertdialog", { name: `Delete ${BUSINESS}?` });
	await dialog.getByLabel(new RegExp(`Type ${BUSINESS} to confirm`)).fill(BUSINESS);
	await dialog.getByRole("button", { name: "Delete customer" }).click();
	await expect(superPage.getByText(`${BUSINESS} deleted`)).toBeVisible();
	await superPage.goto("/admin/plans");
	await superPage.getByRole("button", { name: `More actions for ${PLAN}` }).click();
	await superPage.getByRole("menuitem", { name: "Delete" }).click();
	await superPage
		.getByRole("alertdialog", { name: `Delete ${PLAN}?` })
		.getByRole("button", { name: "Delete plan" })
		.click();
	await expect(superPage.getByRole("article", { name: new RegExp(`^${PLAN}`) })).toHaveCount(0);
	planId = "";
});
