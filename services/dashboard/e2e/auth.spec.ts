import { expect, type Page, test } from "@playwright/test";
import { E2E_PEOPLE, ensureMember, ensureSuperadminPassword, ensureTenant, setSignupMode } from "./fixtures/accounts";
import { ACCOUNTS_READY, ADMIN_KEY, adminCall, ENGINE, publicCall } from "./fixtures/stack";
import { expectNoAxeViolations } from "./utils/axe";
import { latestCodeFor } from "./utils/mail-log";
import { isPhone, openAdvanced, roleOf } from "./utils/session";

/**
 * D1 auth journeys against the REAL stack. Needs A3–A5 merged, the stack rebuilt, the dev e-mail
 * log provider, RAG_CORS_ORIGINS including http://127.0.0.1:5173, and:
 *   E2E_ACCOUNTS=1 E2E_ADMIN_KEY=… [E2E_SUPERADMIN_EMAIL=…] pnpm e2e
 * Until then every test here is skipped (auth-flows.spec.ts runs the same journeys on the mock).
 */
test.skip(
	!ACCOUNTS_READY || !ADMIN_KEY,
	"accounts backend (A3–A5) not merged yet: set E2E_ACCOUNTS=1 and E2E_ADMIN_KEY",
);

let superadminPassword: string | null = null;

test.beforeAll(async ({ request }) => {
	const tenant = await ensureTenant(request);
	await ensureMember(request, tenant, E2E_PEOPLE.owner, "owner");
	await ensureMember(request, tenant, E2E_PEOPLE.member, "member");
	superadminPassword = await ensureSuperadminPassword(request);
});

/** The main navigation; on a phone it lives in the drawer, so open that first. */
async function mainNav(page: Page) {
	const nav = page.getByRole("navigation", { name: "Main" });
	if (isPhone(page) && !(await nav.isVisible())) await page.getByRole("button", { name: "Open menu" }).click();
	return nav;
}

async function loginAs(page: Page, email: string, password: string) {
	await page.goto("/login");
	await page.getByLabel("Email").fill(email);
	await page.getByLabel("Password", { exact: true }).fill(password);
	await page.getByRole("button", { name: "Log in" }).click();
}

test("each role signs in and sees its own navigation", async ({ page }, info) => {
	const role = roleOf(info);
	if (role === "superadmin") {
		if (superadminPassword) {
			await loginAs(page, E2E_PEOPLE.superadmin.email, superadminPassword);
		} else {
			test.info().annotations.push({ type: "note", description: "No RAG_SUPERADMIN_EMAIL: admin key used instead" });
			await page.goto("/login/key");
			await page.getByLabel("API key").fill(ADMIN_KEY);
			await page.getByRole("button", { name: "Sign in" }).click();
		}
		await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
		await expect((await mainNav(page)).getByRole("link", { name: "Customers" })).toBeVisible();
	} else {
		const p = E2E_PEOPLE[role];
		await loginAs(page, p.email, p.password);
		await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
		const nav = await mainNav(page);
		await expect(nav.getByRole("link", { name: "Team" })).toBeVisible();
		await expect(nav.getByRole("link", { name: "Customers" })).toHaveCount(0);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, `${role} overview`);
});

test("wrong password → message", async ({ page }) => {
	await loginAs(page, E2E_PEOPLE.owner.email, "definitely not it 123");
	await expect(page.getByRole("alert")).toContainText("Email or password isn't right.");
});

test("sign-up (Approval) → code from the log → verify → pending → activate → Check again → Overview", async ({
	page,
	request,
}, info) => {
	test.skip(roleOf(info) !== "owner", "one run is enough");
	await setSignupMode(request, "approval");
	const email = `anand.${Date.now()}@ananddental.in`;
	await page.goto("/signup");
	await page.getByLabel("Business name").fill("Anand Dental Care");
	await page.getByLabel("Your name").fill("Anand Joshi");
	await page.getByLabel("Work email").fill(email);
	await page.getByLabel("Password", { exact: true }).fill("Kothrud clinic e2e 2026");
	await page.getByRole("button", { name: "Create account" }).click();
	await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
	await expect.poll(() => latestCodeFor(email), { timeout: 20_000 }).not.toBeNull();
	await page.getByLabel("Digit 1 of 6").click();
	await page.keyboard.type(latestCodeFor(email) ?? "");
	await expect(page.getByRole("heading", { name: "We'll activate your workspace soon" })).toBeVisible();
	const tenants = await adminCall<{ items: { id: string; name: string }[] }>(
		request,
		"GET",
		"/api/v1/admin/tenants?status=pending&search=Anand",
	);
	const t = tenants.body.items.find((x) => x.name === "Anand Dental Care");
	expect(t).toBeTruthy();
	await adminCall(request, "POST", `/api/v1/admin/tenants/${t?.id}/activate`, { note: "Paid ₹3,000 via UPI (e2e)" });
	await page.getByRole("button", { name: "Check again" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	await adminCall(
		request,
		"DELETE",
		`/api/v1/admin/tenants/${t?.id}?confirm_name=${encodeURIComponent("Anand Dental Care")}`,
	);
});

test("forgot → reset → login with the new password", async ({ page }, info) => {
	test.skip(roleOf(info) !== "member", "one run is enough");
	const p = E2E_PEOPLE.member;
	const next = `${p.password} x`;
	await page.goto("/forgot");
	await page.getByLabel("Email").fill(p.email);
	await page.getByRole("button", { name: "Send code" }).click();
	await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
	await expect.poll(() => latestCodeFor(p.email), { timeout: 20_000 }).not.toBeNull();
	await page.getByLabel("Digit 1 of 6").click();
	await page.keyboard.type(latestCodeFor(p.email) ?? "");
	await page.getByLabel("New password", { exact: true }).fill(next);
	await page.getByLabel("Repeat new password").fill(next);
	await page.getByRole("button", { name: "Save new password" }).click();
	await page.getByLabel("Password", { exact: true }).fill(next);
	await page.getByRole("button", { name: "Log in" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	// Put the original password back (change-password: a second e-mailed code would hit the
	// one-code-per-minute limit), so a re-run and the other projects still sign in with
	// E2E_PEOPLE.member.password.
	const login = await publicCall<{ access_token: string }>(page.request, "/api/v1/auth/login", {
		email: p.email,
		password: next,
	});
	expect(login.status).toBe(200);
	const back = await page.request.post(`${ENGINE}/api/v1/auth/change-password`, {
		headers: { Authorization: `Bearer ${login.body.access_token}` },
		data: { current_password: next, new_password: p.password },
	});
	expect(back.status()).toBe(204);
});

test("invitation link (accept_url) → new account → tenant view", async ({ page, request }, info) => {
	test.skip(roleOf(info) !== "owner", "one run is enough");
	const tenant = await ensureTenant(request);
	const email = `asha.${Date.now()}@shree-labs.in`;
	const inv = await adminCall<{ accept_url: string }>(
		request,
		"POST",
		`/api/v1/admin/tenants/${tenant.id}/invitations`,
		{
			email,
			role: "member",
		},
	);
	const url = new URL(inv.body.accept_url);
	await page.goto(`/invite${url.hash}`);
	await expect(page.getByRole("heading", { name: "Join Shree Labs" })).toBeVisible();
	expect(new URL(page.url()).hash).toBe("");
	await page.getByLabel("Your name").fill("Asha Pawar");
	await page.getByLabel("Password", { exact: true }).fill("Front desk e2e 2026");
	await page.getByLabel("Repeat new password").fill("Front desk e2e 2026");
	await page.getByRole("button", { name: "Accept and join" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	await expect((await mainNav(page)).getByRole("link", { name: "Team" })).toBeVisible();
});

test("closed mode → Talk to us", async ({ page, request }, info) => {
	test.skip(roleOf(info) !== "superadmin", "one run is enough");
	await setSignupMode(request, "closed");
	await page.goto("/signup");
	await expect(page.getByRole("heading", { name: "Talk to us to get started" })).toBeVisible();
	await setSignupMode(request, "approval");
});

test("API key sign-in: admin key yes, ck_ key refused", async ({ page, request }, info) => {
	test.skip(roleOf(info) !== "superadmin", "one run is enough");
	const tenant = await ensureTenant(request);
	const kb = await adminCall<{ api_key?: string }>(request, "POST", "/api/v1/corpora", {
		name: `E2E key check ${Date.now()}`,
		tenant_id: tenant.id,
	});
	await page.goto("/login/key");
	await page.getByLabel("API key").fill(kb.body.api_key ?? "ck_missing");
	await page.getByRole("button", { name: "Sign in" }).click();
	await expect(page.getByRole("alert")).toContainText("Knowledge-base keys don't open the dashboard");
	await page.getByLabel("API key").fill(ADMIN_KEY);
	await page.getByRole("button", { name: "Sign in" }).click();
	await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
	await expect((await mainNav(page)).getByRole("link", { name: "Customers" })).toBeVisible();
});

test("access token expiry (page.clock past 15 min) refreshes transparently", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "one run is enough");
	await page.clock.install();
	await loginAs(page, E2E_PEOPLE.owner.email, E2E_PEOPLE.owner.password);
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	const refreshed = page.waitForResponse((r) => r.url() === `${ENGINE}/api/v1/auth/refresh` && r.status() === 200);
	await page.clock.fastForward("16:00");
	await refreshed;
	await openAdvanced(page);
	await (await mainNav(page)).getByRole("link", { name: "Usage", exact: true }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Usage" })).toBeVisible();
});

test('two tabs with "Keep me signed in" refreshing at once both stay signed in', async ({ page, context }, info) => {
	test.skip(roleOf(info) !== "owner", "one run is enough");
	await page.goto("/login");
	await page.getByLabel("Email").fill(E2E_PEOPLE.owner.email);
	await page.getByLabel("Password", { exact: true }).fill(E2E_PEOPLE.owner.password);
	await page.getByRole("checkbox", { name: /Keep me signed in/ }).check();
	await page.getByRole("button", { name: "Log in" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	const second = await context.newPage();
	await second.goto("/usage");
	const reused: string[] = [];
	for (const p of [page, second]) {
		p.on("response", async (r) => {
			if (r.url().endsWith("/api/v1/auth/refresh") && r.status() === 401) reused.push(await r.text());
		});
	}
	await Promise.all([page.reload(), second.reload()]);
	await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	await expect(second.getByRole("heading", { level: 1, name: "Usage" })).toBeVisible();
	expect(reused).toEqual([]);
});
