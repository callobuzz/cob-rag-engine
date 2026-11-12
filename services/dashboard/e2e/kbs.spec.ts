import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * D3 knowledge bases on the mock engine (e2e/fixtures/mock-kbs.ts), across the README matrix.
 * The mock follows A4/A5/A6 contracts (`owner_required`, `kb_limit_reached`, active-tenant list,
 * `/tenants/me/plan`); the same journeys against the real stack wait for A5/A6 to merge.
 */

const PATIENT = "7b221003-0000-4000-8000-000000000001";
let world: World;

/** No horizontal page scroll at 390 px, including inside the shell's main column. */
async function noSideScroll(page: Page, name: string) {
	if (!isPhone(page)) return;
	const widths = await page.evaluate(() => ({
		doc: document.documentElement.scrollWidth,
		main: document.getElementById("main")?.getBoundingClientRect().width ?? 0,
	}));
	expect(widths.doc, name).toBeLessThanOrEqual(390);
	expect(widths.main, name).toBeLessThanOrEqual(390);
}

async function checkPage(page: Page, name: string) {
	await noSideScroll(page, name);
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await page.screenshot({ path: screenshotPath(test.info(), name), fullPage: true });
}

async function openTab(page: Page, tab: string) {
	await page.getByRole("tab", { name: tab, exact: true }).click();
	await expect(page.getByRole("tab", { name: tab, exact: true })).toHaveAttribute("aria-selected", "true");
}

test.describe("knowledge bases", () => {
	test.beforeEach(async ({ context }, info) => {
		world = createWorld(info.title.includes("plan cap") ? { kbLimit: 1 } : {});
		await installMockEngine(context, world);
	});

	test("list and every detail tab render with PoweredBy, axe clean, screenshots", async ({ page }, info) => {
		test.setTimeout(180_000);
		const role = roleOf(info);
		await logIn(page, role);
		await page.goto("/kbs");
		await expect(page.getByRole("heading", { level: 1, name: "Knowledge bases" })).toBeVisible();
		const table = page.getByRole("table", { name: "Knowledge bases" });
		await expect(table.getByRole("link", { name: "Patient help" })).toBeVisible();
		if (role === "superadmin") {
			await expect(table.getByRole("link", { name: "Store assistant" })).toBeVisible();
			if (!isPhone(page)) await expect(table.getByRole("columnheader", { name: "Customer" })).toBeVisible();
		} else {
			await expect(table.getByRole("link", { name: "Store assistant" })).toHaveCount(0);
			await expect(page.getByText(/Your Pro plan allows 3 knowledge bases; you're using 2\./)).toBeVisible();
		}
		if (isPhone(page)) {
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
		}
		await checkPage(page, "kbs");

		await table.getByRole("link", { name: "Patient help" }).click();
		await expect(page.getByRole("heading", { level: 1, name: "Patient help" })).toBeVisible();
		await expect(page.getByText("Answer confidence", { exact: true })).toBeVisible();
		await checkPage(page, "kb-overview");
		for (const [tab, slug, marker] of [
			["Model & keys", "kb-model", "Which key pays for an answer"],
			["Assistant", "kb-assistant", "What visitors see"],
			["Retrieval", "kb-retrieval", "Current: Standard"],
			["Embedding", "kb-embedding", "Embedding model"],
			["API keys", "kb-apikey", "API keys"],
			["Danger zone", "kb-danger", "Delete this knowledge base"],
		] as const) {
			await openTab(page, tab);
			await expect(page.getByText(marker, { exact: true }).first()).toBeVisible();
			if (isPhone(page)) {
				expect(await page.evaluate(() => document.documentElement.scrollWidth), slug).toBeLessThanOrEqual(390);
			}
			await checkPage(page, slug);
		}
	});

	test("owner: create, prompt, retrieval, rotate, delete", async ({ page }, info) => {
		test.skip(roleOf(info) !== "owner", "owner journey");
		test.setTimeout(120_000);
		await logIn(page, "owner");
		await page.goto("/kbs");
		await page.getByRole("button", { name: "New knowledge base" }).click();
		const dialog = page.getByRole("dialog");
		await dialog.getByLabel("Name").fill("Home collection help");
		await expect(dialog.getByRole("radio", { name: /Standard/ })).toHaveAttribute("aria-checked", "true");
		await expectNoAxeViolations(page, "create dialog");
		await dialog.getByRole("button", { name: "Create knowledge base" }).click();
		const key = dialog.getByText(/^ck_[0-9a-f]{8}_mockSecretShownOnce$/);
		await expect(key).toBeVisible();
		await expect(dialog.getByText(/people log in with their own accounts/)).toBeVisible();
		await page.screenshot({ path: screenshotPath(info, "kb-created") });
		await expectNoAxeViolations(page, "key shown once");
		await dialog.getByRole("button", { name: "Open knowledge base" }).click();
		await expect(page.getByRole("heading", { level: 1, name: "Home collection help" })).toBeVisible();
		await expect(page.getByText(/mockSecretShownOnce/)).toHaveCount(0);

		// The classic instructions live in the Assistant tab while the assistant is switched off.
		await openTab(page, "Assistant");
		await page.getByRole("button", { name: "Turn the assistant off" }).click();
		await page.getByRole("button", { name: "Turn it off" }).click();
		await expect(page.getByText(/The assistant is off./)).toBeVisible();
		await page
			.getByRole("textbox", { name: "Instructions" })
			.fill("You are the assistant for Shree Labs, a pathology lab in Pune. Answer only from the lab's documents.");
		await page.getByRole("button", { name: "Save changes" }).click();
		await expect(page.getByText("Prompt saved")).toBeVisible();
		const promptPut = world.kbs.sent.at(-1)?.body as Record<string, unknown>;
		expect(Object.keys(promptPut)).toEqual(["prompt_config"]);

		await openTab(page, "Retrieval");
		// Unset fields show what the engine runs, not "off".
		await expect(page.getByRole("switch", { name: "Keyword search alongside meaning search" })).toBeChecked();
		await expect(page.getByText("default: Weighted scores 0.75 (engine)", { exact: true })).toBeVisible();
		const finalK = page.getByLabel("Passages given to the model", { exact: true });
		await finalK.fill("6");
		await page.getByRole("button", { name: "Save changes" }).click();
		await expect(page.getByText("Retrieval settings saved")).toBeVisible();
		expect(world.kbs.sent.at(-1)?.body).toEqual({ retrieval_config: { final_k: 6 } });
		await expect(page.getByText("Current: custom")).toBeVisible();
		await page.getByRole("button", { name: "Reset Passages given to the model" }).click();
		await expect.poll(() => world.kbs.sent.at(-1)?.body).toEqual({ retrieval_config: { final_k: null } });
		await expect(finalK).toHaveValue("");

		await openTab(page, "API keys");
		// A new knowledge base starts with its "Default key": never used, so no live-use warning.
		await expect(page.getByRole("rowheader", { name: /Default key/ })).toBeVisible();
		await page.getByRole("button", { name: "Rotate Default key" }).click();
		const rot = page.getByRole("alertdialog", { name: "Rotate Default key?" });
		await expect(rot.getByTestId("rotate-live-warning")).toHaveCount(0);
		await rot.getByRole("button", { name: "Rotate key" }).click();
		const shown = page.getByRole("dialog", { name: "New key: Default key" });
		await expect(shown.getByText(/_mockRotatedSecret\d+$/)).toBeVisible();
		await expectNoAxeViolations(page, "rotated key");
		await shown.getByRole("button", { name: "Done" }).click();

		// A new write key, shown once; then lowered, and revoked with the typed word.
		await page.getByRole("button", { name: "Create key" }).click();
		const create = page.getByRole("dialog", { name: "Create a key" });
		await create.getByLabel("Name").fill("Billing sync");
		await create.getByRole("radio", { name: /^Write/ }).check();
		await create.getByRole("button", { name: "Create key" }).click();
		const made = page.getByRole("dialog", { name: "New key: Billing sync" });
		await expect(made.getByText(/_mockNewSecret\d+$/)).toBeVisible();
		expect(world.apiKeys.sent.at(-1)?.body).toEqual({ name: "Billing sync", permission: "write" });
		await made.getByRole("button", { name: "Done" }).click();
		await page.getByRole("button", { name: "Edit Billing sync" }).click();
		const edit = page.getByRole("dialog", { name: "Edit Billing sync" });
		await edit.getByRole("radio", { name: /^Read/ }).check();
		await expect(edit.getByTestId("edit-lower-warning")).toBeVisible();
		await edit.getByRole("button", { name: "Save changes" }).click();
		await expect(page.getByRole("button", { name: "Edit Billing sync" })).toBeVisible();
		expect(world.apiKeys.sent.at(-1)?.body).toEqual({ permission: "read" });
		await page.getByRole("button", { name: "Revoke Billing sync" }).click();
		const revoke = page.getByRole("alertdialog", { name: "Revoke Billing sync?" });
		await expect(revoke.getByRole("button", { name: "Revoke key" })).toBeDisabled();
		await revoke.getByRole("textbox").fill("revoke");
		await revoke.getByRole("button", { name: "Revoke key" }).click();
		await expect(page.getByRole("rowheader", { name: /Billing sync/ })).toHaveCount(0);

		await openTab(page, "Danger zone");
		await page.getByRole("button", { name: "Delete Home collection help" }).click();
		const del = page.getByRole("alertdialog");
		const go = del.getByRole("button", { name: "Delete Home collection help" });
		await del.getByRole("textbox").fill("home collection help");
		await expect(go).toBeDisabled();
		await del.getByRole("textbox").fill("Home collection help");
		await go.click();
		await expect(page.getByRole("heading", { level: 1, name: "Knowledge bases" })).toBeVisible();
		await expect(page.getByRole("link", { name: "Home collection help" })).toHaveCount(0);
	});

	test("owner: a key used in the last 24 hours warns before it is rotated or revoked", async ({ page }, info) => {
		test.skip(roleOf(info) !== "owner", "owner journey");
		await logIn(page, "owner");
		await page.goto(`/kbs/${PATIENT}?tab=key`);
		await expect(page.getByRole("rowheader", { name: /Agent – Claude Code/ })).toBeVisible();
		await page.getByRole("button", { name: "Rotate Agent – Claude Code" }).click();
		const rot = page.getByRole("alertdialog", { name: "Rotate Agent – Claude Code?" });
		await expect(rot.getByTestId("rotate-live-warning")).toContainText("Something live is probably using it");
		await expectNoAxeViolations(page, "rotate live warning");
		await rot.getByRole("button", { name: "Cancel" }).click();
		await page.getByRole("button", { name: "Revoke Agent – Claude Code" }).click();
		const revoke = page.getByRole("alertdialog", { name: "Revoke Agent – Claude Code?" });
		await expect(revoke.getByTestId("revoke-live-warning")).toBeVisible();
		await revoke.getByRole("button", { name: "Cancel" }).click();
		// nothing was sent: only reads so far
		expect(world.apiKeys.sent).toEqual([]);
	});

	test("member: prompt saves; model, rotate and delete are locked", async ({ page }, info) => {
		test.skip(roleOf(info) !== "member", "member gating");
		await logIn(page, "member");
		// The classic instructions are shown while the assistant is switched off.
		const patient = world.assistant.byKb.get(PATIENT);
		if (patient?.published) patient.published.config.enabled = false;
		await page.goto(`/kbs/${PATIENT}?tab=assistant`);
		await page.getByLabel("Rules").fill("Never give medical advice or interpret results.");
		await page.getByRole("button", { name: "Save changes" }).click();
		await expect(page.getByText("Prompt saved")).toBeVisible();

		await openTab(page, "Model & keys");
		await expect(page.getByLabel("Model", { exact: true })).toHaveAttribute("readonly", "");
		const save = page.getByRole("button", { name: "Save changes" });
		await expect(save).toHaveAttribute("aria-disabled", "true");
		// aria-disabled (never silently disabled): the click still reaches the gate and shows the toast.
		await save.click({ force: true });
		await expect(page.getByText("Only owners can set the model and its key").first()).toBeVisible();
		await checkPage(page, "kb-model-member");

		await openTab(page, "API keys");
		await expect(page.getByRole("button", { name: "Create key" })).toHaveAttribute("aria-disabled", "true");
		await expect(page.getByText(/You're a Member/)).toBeVisible();
		await expect(page.getByRole("table", { name: "API keys" })).toHaveCount(0);
		await openTab(page, "Danger zone");
		await expect(page.getByRole("button", { name: "Delete Patient help" })).toHaveAttribute("aria-disabled", "true");
		await page.getByRole("button", { name: "Delete Patient help" }).click({ force: true });
		await expect(page.getByRole("alertdialog")).toHaveCount(0);
		await page.goto("/kbs");
		await expect(page.getByRole("button", { name: "New knowledge base" })).toHaveAttribute("aria-disabled", "true");
	});

	test("super admin: create for Rangoli Sarees needs a customer; move between customers", async ({ page }, info) => {
		test.skip(roleOf(info) !== "superadmin", "admin journey");
		test.setTimeout(90_000);
		await logIn(page, "superadmin");
		await page.goto("/kbs");
		await page.getByRole("button", { name: "New knowledge base" }).click();
		const dialog = page.getByRole("dialog");
		await dialog.getByLabel("Name").fill("Saree care FAQ");
		await dialog.getByRole("button", { name: "Create knowledge base" }).click();
		await expect(dialog.getByText("Pick the customer this knowledge base belongs to.")).toBeVisible();
		await dialog.getByRole("combobox", { name: "Customer" }).selectOption({ label: "Rangoli Sarees" });
		await dialog.getByRole("button", { name: "Create knowledge base" }).click();
		await expect(dialog.getByText(/_mockSecretShownOnce$/)).toBeVisible();
		expect(world.kbs.sent.at(-1)?.body).toMatchObject({
			name: "Saree care FAQ",
			retrieval_config: { preset: "standard" },
		});
		await dialog.getByRole("button", { name: "Done" }).click();

		await page.getByRole("combobox", { name: "Customer" }).selectOption({ label: "Rangoli Sarees" });
		await expect(page).toHaveURL(/tenant=/);
		await expect(page.getByRole("link", { name: "Saree care FAQ" })).toBeVisible();
		await expect(page.getByRole("link", { name: "Patient help" })).toHaveCount(0);

		await page.goto("/kbs/91f0e6b2-0000-4000-8000-000000000004?tab=danger");
		await expect(page.getByRole("heading", { level: 1, name: "Admissions help" })).toBeVisible();
		await page.getByLabel("New customer").selectOption({ label: "Shree Labs" });
		await page.getByRole("button", { name: "Move", exact: true }).click();
		const confirm = page.getByRole("alertdialog", { name: "Move Admissions help to Shree Labs?" });
		await expectNoAxeViolations(page, "move dialog");
		await confirm.getByRole("button", { name: "Move" }).click();
		await expect(page.getByText("Moved to Shree Labs")).toBeVisible();
		await expect(page.getByText(/^Shree Labs/).first()).toBeVisible();
	});

	test("plan cap: a second knowledge base shows kb_limit_reached", async ({ page }, info) => {
		test.skip(!["owner"].includes(roleOf(info)), "tenant owner with a Starter plan");
		await logIn(page, "owner");
		await page.goto("/kbs");
		await expect(page.getByText(/Your Starter plan allows 1 knowledge bases/)).toBeVisible();
		await page.getByRole("button", { name: "New knowledge base" }).click();
		const dialog = page.getByRole("dialog");
		await dialog.getByLabel("Name").fill("Staff SOPs 2");
		await dialog.getByRole("button", { name: "Create knowledge base" }).click();
		await expect(dialog.getByText("Your plan allows 1 knowledge bases.")).toBeVisible();
		await expect(dialog.getByRole("link", { name: "See plan" })).toBeVisible();
		await expectNoAxeViolations(page, "kb limit");
	});
});
