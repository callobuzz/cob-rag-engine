import { expect, type Page, test } from "@playwright/test";
import { IMPROVED_RULE } from "./fixtures/mock-assistant";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * C3: the Assistant tab and the preview console on the mock engine (e2e/fixtures/mock-assistant.ts),
 * across the README matrix. Indian demo data: Shree Labs, "Patient help".
 */
const PATIENT = "7b221003-0000-4000-8000-000000000001";
let world: World;

async function goToSection(page: Page, name: string, value: string) {
	if (isPhone(page)) await page.getByLabel("Section").selectOption(value);
	else
		await page.getByRole("navigation", { name: "Assistant settings" }).getByRole("link", { name, exact: true }).click();
}

async function checkPage(page: Page, name: string, info: Parameters<typeof screenshotPath>[0]) {
	if (isPhone(page))
		expect(await page.evaluate(() => document.documentElement.scrollWidth), name).toBeLessThanOrEqual(390);
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await page.screenshot({ path: screenshotPath(info, name), fullPage: true });
}

test.describe("assistant", () => {
	test.beforeEach(async ({ context, page }, info) => {
		world = createWorld();
		await installMockEngine(context, world);
		await logIn(page, roleOf(info));
	});

	test("edit identity, preview with the draft, then publish (owner) or find it locked (member)", async ({
		page,
	}, info) => {
		test.setTimeout(120_000);
		const role = roleOf(info);
		await page.goto(`/kbs/${PATIENT}?tab=assistant`);
		await expect(page.getByRole("tab", { name: "Assistant", selected: true })).toBeVisible();
		await expect(page.getByText("Classic instructions are not used while the assistant is on.")).toBeVisible();
		await checkPage(page, "assistant-overview", info);

		await goToSection(page, "Identity", "identity");
		const name = page.getByRole("textbox", { name: "Assistant name" });
		await name.fill("Meera");
		await page.getByRole("button", { name: "Save draft" }).click();
		await expect(page.getByText("Draft saved. Preview it, then publish when ready.")).toBeVisible();
		expect(world.assistant.saves.at(-1)?.base_revision).toBe(3);
		await checkPage(page, "assistant-identity", info);

		await page.getByRole("button", { name: "Preview", exact: true }).click();
		await page.getByRole("textbox", { name: "Message" }).fill("Lipid profile ka rate kya hai?");
		await page.getByRole("button", { name: "Send" }).click();
		await expect(page.getByRole("region", { name: "Table" }).first()).toBeVisible();
		const trace = page.getByRole("complementary", { name: "How this answer was made" });
		if (role === "member") {
			await expect(trace.getByText("Only owners see how the answer was made (sources, scores, prompt).")).toBeVisible();
			await expect(trace.getByText("Guidance rules used")).toHaveCount(0);
		} else {
			await expect(trace.getByTestId("trace-top")).toHaveText("0.62");
			await expect(trace.getByTestId("trace-threshold")).toHaveText("0.45");
			await expect(trace.getByText("Removed", { exact: true })).toBeVisible();
		}
		// The public stage times show under the answer; the finer ones stay in the owner trace.
		await expect(page.getByTestId("time-understand")).toBeVisible();
		await expect(page.getByTestId("time-write")).toBeVisible();
		await checkPage(page, "assistant-preview", info);

		if (role === "member") {
			await page.getByRole("button", { name: "Publish", exact: true }).click({ force: true });
			await expect(page.getByText("Only owners can publish the assistant").first()).toBeVisible();
		} else {
			await page.getByRole("button", { name: "Publish", exact: true }).click();
			await page.getByRole("button", { name: "Publish now" }).click();
			await expect(page.getByText(/Published version 4/)).toBeVisible();
		}
	});

	test("improve a guidance rule: a suggestion first, written into the draft only when used", async ({ page }, info) => {
		await page.goto(`/kbs/${PATIENT}?tab=assistant`);
		await goToSection(page, "Guidance rules", "rules");
		await page.getByRole("button", { name: "Improve rule 1" }).click();
		const dialog = page.getByRole("dialog", { name: "Improve this rule" });
		await expect(dialog.getByText(IMPROVED_RULE)).toBeVisible();
		await expect(dialog.getByText("price-exact")).toBeVisible();
		expect(world.assistant.improveCalls).toHaveLength(1);
		expect(world.assistant.improveCalls[0]).toMatchObject({ type: "compliance", rule_id: "no-diagnosis" });
		await checkPage(page, "assistant-improve-rule", info);
		expect(world.assistant.saves).toHaveLength(0);

		await dialog.getByRole("button", { name: "Use this wording" }).click();
		await expect(dialog).toHaveCount(0);
		expect(world.assistant.saves).toHaveLength(0); // using it only fills the form
		await page.getByRole("button", { name: "Save draft" }).click();
		await expect(page.getByText("Draft saved. Preview it, then publish when ready.")).toBeVisible();
		const saved = world.assistant.saves.at(-1)?.config.rules as { id: string; instruction: string }[];
		expect(saved[0]?.instruction).toBe(IMPROVED_RULE);
	});

	test("qualification rules are written in the Leads section and saved with the draft", async ({ page }, info) => {
		await page.goto(`/kbs/${PATIENT}?tab=assistant`);
		await goToSection(page, "Leads", "leads");
		await page.getByRole("button", { name: "Add a rule" }).click();
		await page.getByRole("button", { name: "Add a condition to rule 1" }).click();
		await page.getByRole("combobox", { name: "Condition 2: about" }).selectOption("phone");
		await page.getByRole("combobox", { name: "Condition 2: is" }).selectOption("present");
		await expect(page.getByLabel("Condition 2: value")).toHaveCount(0);
		await checkPage(page, "assistant-qualification", info);
		await page.getByRole("button", { name: "Save draft" }).click();
		await expect(page.getByText("Draft saved. Preview it, then publish when ready.")).toBeVisible();
		const saved = world.assistant.saves.at(-1)?.config.leads as { qualification: { conditions: unknown[] }[] };
		expect(saved.qualification).toEqual([
			{
				status: "qualified",
				conditions: [
					{ field: "intent", op: "eq", value: "buying_intent" },
					{ field: "phone", op: "present", value: null },
				],
			},
		]);
	});

	test("a greeting is answered without sources", async ({ page }) => {
		await page.goto(`/kbs/${PATIENT}?tab=assistant&section=preview`);
		await page.getByRole("textbox", { name: "Message" }).fill("Namaste");
		await page.getByRole("button", { name: "Send" }).click();
		await expect(page.getByTestId("bot-answer")).toContainText("Main Asha hoon");
		await expect(page.getByRole("link", { name: /Source 1/ })).toHaveCount(0);
	});

	test("suggestion chips ask the next question and the next-step button can be dismissed", async ({ page }) => {
		await page.goto(`/kbs/${PATIENT}?tab=assistant&section=preview`);
		await page.getByRole("textbox", { name: "Message" }).fill("I want to book a home visit");
		await page.getByRole("button", { name: "Send" }).click();
		const call = page.getByRole("link", { name: /Call us/ });
		await expect(call).toHaveAttribute("href", "tel:+919822012345");
		await expect(call).toHaveAttribute("target", "_blank");
		await page.getByRole("button", { name: "Not now" }).click();
		await expect(call).toHaveCount(0);
		const suggestions = page.getByRole("group", { name: "Suggestions" });
		await suggestions.getByRole("button", { name: "Home collection" }).click();
		await expect(page.getByText("Do you collect samples at home?").first()).toBeVisible();
		// Only the newest answer keeps its chips.
		await expect(suggestions).toHaveCount(1);
	});

	test("wide tables scroll inside the answer on phones, never the page", async ({ page }) => {
		test.skip(!isPhone(page), "phone projects only");
		await page.goto(`/kbs/${PATIENT}?tab=assistant&section=preview`);
		await page.getByRole("textbox", { name: "Message" }).fill("Compare all test prices");
		await page.getByRole("button", { name: "Send" }).click();
		await expect(page.getByRole("region", { name: "Table" }).first()).toBeVisible();
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
	});

	test("a draft someone else saved is a conflict, never an overwrite", async ({ page }) => {
		await page.goto(`/kbs/${PATIENT}?tab=assistant&section=identity`);
		const name = page.getByRole("textbox", { name: "Assistant name" });
		await expect(name).toBeVisible();
		// Someone else saves after this page loaded the draft.
		const draft = world.assistant.byKb.get(PATIENT)?.draft;
		if (draft) draft.draft_revision = 9;
		await name.fill("Meera");
		await page.getByRole("button", { name: "Save draft" }).click();
		await expect(page.getByRole("button", { name: "Load their version" })).toBeVisible();
		expect(world.assistant.byKb.get(PATIENT)?.draft?.draft_revision).toBe(9);
	});

	test("old ?tab=prompt links land on the Assistant tab", async ({ page }) => {
		await page.goto(`/kbs/${PATIENT}?tab=prompt`);
		await expect(page.getByRole("tab", { name: "Assistant", selected: true })).toBeVisible();
	});

	test("versions: owners restore an older one, members can only view", async ({ page }, info) => {
		const role = roleOf(info);
		await page.goto(`/kbs/${PATIENT}?tab=assistant&section=versions`);
		const row = page.getByRole("row", { name: /Version 2/ });
		await expect(row).toBeVisible();
		if (role === "member") {
			await expect(row.getByRole("button", { name: "Restore" })).toHaveAttribute("aria-disabled", "true");
		} else {
			await row.getByRole("button", { name: "Restore" }).click();
			await page.getByRole("button", { name: "Restore version 2" }).click();
			await expect(page.getByText(/Restored version 2 as version 4/)).toBeVisible();
		}
		await checkPage(page, "assistant-versions", info);
	});
});
