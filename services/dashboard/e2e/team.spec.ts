import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * D7 Team (tenant view) on the mock engine: invite by e-mail (the link read from the mock's mail
 * log, like `docker compose logs mailer`), accept in another browser, change role, remove, the
 * last-owner lock; a member's read-only view and leaving. The same journeys against the running
 * stack are pending at the bottom of the file.
 */

let world: World;

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		const width = await page.evaluate(() => document.documentElement.scrollWidth);
		expect(width, name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

const rowOf = (page: Page, name: string) =>
	page.getByRole("row").filter({ has: page.getByRole("rowheader", { name: new RegExp(name) }) });

test.beforeEach(async ({ context }) => {
	world = createWorld();
	await installMockEngine(context, world);
});

test("owner: last-owner lock, invite by e-mail, accept, change role, remove", async ({ page, browser }, info) => {
	test.skip(roleOf(info) !== "owner", "tenant owner journey");
	test.setTimeout(120_000);
	await logIn(page, "owner");
	await page.goto("/team");
	await expect(page.getByRole("heading", { level: 1, name: "Team" })).toBeVisible();
	const priya = rowOf(page, "Priya Kulkarni");
	await expect(priya.getByText("Last owner")).toBeVisible();
	await expect(priya.getByRole("button", { name: "Leave workspace" })).toHaveCount(0);
	await expect(page.getByText("Priya Kulkarni is the only owner")).toBeVisible();
	await checkPage(page, "team-owner");

	await page.getByRole("button", { name: "Invite teammate" }).click();
	const dialog = page.getByRole("dialog", { name: "Invite a teammate to Shree Labs" });
	await dialog.getByLabel("Email").fill("asha.pawar@shree-labs.in");
	await expectNoAxeViolations(page, "invite dialog");
	await fullShot(page, screenshotPath(info, "team-invite"));
	await dialog.getByRole("button", { name: "Send invitation" }).click();
	await expect(dialog).toBeHidden();
	await expect(page.getByRole("list", { name: "Pending invitations" })).toContainText("asha.pawar@shree-labs.in");
	const mail = world.accounts.mail.find((m) => m.to === "asha.pawar@shree-labs.in");
	expect(mail?.link).toMatch(/\/invite#token=inv_/);

	const other = await browser.newContext();
	await installMockEngine(other, world);
	const asha = await other.newPage();
	await asha.goto((mail?.link ?? "").replace("http://127.0.0.1:5173", ""));
	await expect(asha.getByRole("heading", { name: "Join Shree Labs" })).toBeVisible();
	await asha.getByLabel("Your name").fill("Asha Pawar");
	await asha.getByLabel("Password", { exact: true }).fill("Front desk 2026 ok");
	await asha.getByLabel("Repeat new password").fill("Front desk 2026 ok");
	await asha.getByRole("button", { name: "Accept and join" }).click();
	await expect(asha.getByRole("heading", { level: 1, name: "Home" })).toBeVisible(); // BIZ3: a business lands on Home
	await other.close();

	await page.reload();
	const row = rowOf(page, "Asha Pawar");
	await expect(row).toContainText("Member");
	await row.getByRole("button", { name: "Make owner Asha Pawar" }).click();
	await expect(page.getByText("Asha Pawar is now an owner")).toBeVisible();
	await expect(row).toContainText("Owner");
	await expect(priya.getByText("Last owner")).toHaveCount(0);
	await row.getByRole("button", { name: "Make member Asha Pawar" }).click();
	await expect(page.getByText("Asha Pawar is now a member")).toBeVisible();
	await row.getByRole("button", { name: "Remove Asha Pawar" }).click();
	const confirm = page.getByRole("alertdialog", { name: "Remove Asha Pawar from Shree Labs?" });
	await expectNoAxeViolations(page, "remove dialog");
	await confirm.getByRole("button", { name: "Remove" }).click();
	await expect(rowOf(page, "Asha Pawar")).toHaveCount(0);
	expect(world.users.find((u) => u.email === "asha.pawar@shree-labs.in")?.memberships).toEqual([]);
});

test("member: read-only team, owner toast, leave the workspace", async ({ page }, info) => {
	test.skip(roleOf(info) !== "member", "tenant member journey");
	await logIn(page, "member");
	await page.goto("/team");
	await expect(page.getByText(/You're a Member. Only owners can invite/)).toBeVisible();
	const invite = page.getByRole("button", { name: "Invite teammate" });
	await expect(invite).toHaveAttribute("aria-disabled", "true");
	await invite.click({ force: true });
	await expect(page.getByText("Only owners can invite or change teammates").first()).toBeVisible();
	await expect(page.getByRole("dialog")).toHaveCount(0);
	await expect(rowOf(page, "Priya Kulkarni").getByText("Last owner")).toBeVisible();
	const me = rowOf(page, "Rohan Deshmukh");
	await expect(me.getByText("you")).toBeVisible();
	await checkPage(page, "team-member");
	await me.getByRole("button", { name: "Leave workspace" }).click();
	const dialog = page.getByRole("alertdialog", { name: "Leave Shree Labs?" });
	await expectNoAxeViolations(page, "leave dialog");
	await dialog.getByRole("button", { name: "Leave workspace" }).click();
	await expect(page.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
	expect(world.users.find((u) => u.email === "rohan@shree-labs.in")?.memberships).toEqual([]);
});

test("owners' palette action opens the invite dialog", async ({ page }, info) => {
	test.skip(roleOf(info) !== "owner", "tenant owner");
	await logIn(page, "owner");
	await page.keyboard.press("Control+k");
	const palette = page.getByRole("dialog", { name: "Command palette" });
	await palette.getByRole("combobox").fill("invite");
	await page.keyboard.press("Enter");
	await expect(page.getByRole("dialog", { name: "Invite a teammate to Shree Labs" })).toBeVisible();
});

test("the super admin has no Team page", async ({ page }, info) => {
	test.skip(roleOf(info) !== "superadmin", "admin view");
	await logIn(page, "superadmin");
	await page.goto("/team");
	await expect(page.getByRole("heading", { name: "Not available for your account" })).toBeVisible();
});

/**
 * Against the running stack: needs A5 merged, the stack rebuilt and the dev e-mail log provider
 * (the invitation link is read from `docker compose logs mailer`).
 */
test.describe("team on the real engine", () => {
	test.fixme("owner invites Rohan (link from the mailer log), changes role, removes; last owner can't leave; member read-only (needs the running stack)", async () => {});
});
