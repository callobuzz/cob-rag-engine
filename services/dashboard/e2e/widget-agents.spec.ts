import { expect, type FrameLocator, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { WIDGET_ANSWER } from "./fixtures/mock-widget";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * D6 website widget and Agents (MCP) on the mock engine (e2e/fixtures/mock-widget.ts), across
 * the README matrix. The preview runs the engine's real widget.js (served by the mock from
 * src/callobuzz_rag/widget/static/widget.js) inside public/widget-demo.html, against mocked
 * /widget/config and /widget/chat that check the key, is_active and Origin like the engine.
 * The same journeys against the running stack are pending at the bottom of the file.
 */

const PATIENT = "7b221003-0000-4000-8000-000000000001";
const DASHBOARD_ORIGIN = "http://127.0.0.1:5173";

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

const card = (page: Page, name: string) => page.getByRole("article", { name });
const FRAME = 'iframe[title="Widget preview"]';
const preview = (page: Page): FrameLocator => page.frameLocator(FRAME);

async function clipboard(page: Page): Promise<string> {
	// Windows puts CRLF line ends on the system clipboard.
	return (await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n");
}

test.describe("website widget", () => {
	test.beforeEach(async ({ context, page }, info) => {
		world = createWorld(info.title.includes("plan cap") ? { widgetKeyLimit: 1 } : {});
		await installMockEngine(context, world);
		await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: DASHBOARD_ORIGIN });
		await page.emulateMedia({ reducedMotion: "reduce" });
	});

	test("create, allow the preview, ask, pause, rotate and delete", async ({ page }, info) => {
		test.skip(roleOf(info) === "member", "members are read-only (next test)");
		test.setTimeout(180_000);
		await logIn(page, roleOf(info));

		await page.goto(`/widget?kb=${PATIENT}`);
		await expect(page.getByRole("heading", { level: 1, name: "Website widget" })).toBeVisible();
		await expect(card(page, "Live site")).toBeVisible();
		await expect(card(page, "Live site").getByTestId("limits")).toHaveText("10/min per visitor · 1,000/day");
		if (roleOf(info) === "owner") {
			await expect(page.getByTestId("plan-note")).toHaveText(/1 of 3 widget keys on your plan/);
		} else {
			await expect(page.getByTestId("plan-note")).toHaveCount(0);
		}
		await checkPage(page, "widget-list");

		// Create "Shree Labs site" for https://shree-labs.in with its look.
		await page.getByRole("button", { name: "New widget key" }).click();
		const dialog = page.getByRole("dialog", { name: "New widget key" });
		await dialog.getByLabel("Name").fill("Shree Labs site");
		await dialog.getByLabel("Allowed sites").fill("https://shree-labs.in");
		await dialog.getByLabel("Allowed sites").press("Enter");
		await expect(dialog.getByRole("list", { name: "Sites added" })).toContainText("shree-labs.in");
		await dialog.getByLabel("Title").fill("Shree Labs help");
		await dialog.getByLabel("Greeting").fill("Namaste! Ask about tests, timings or home collection.");
		await dialog.getByLabel("Brand colour", { exact: true }).fill("#0b7a5f");
		await expect(dialog.getByTestId("mock-head")).toHaveText(/Shree Labs help/);
		await expect(dialog.getByTestId("contrast-hint")).toHaveAttribute("data-ok", "true");
		await expectNoAxeViolations(page, "create dialog");
		await fullShot(page, screenshotPath(info, "widget-create"));
		await dialog.getByRole("button", { name: "Create widget key" }).click();
		await expect(dialog).toBeHidden();

		const created = card(page, "Shree Labs site");
		await expect(created).toHaveAttribute("data-selected", "true");
		const snippetBefore = await created.getByTestId("snippet").textContent();
		expect(snippetBefore).toContain('<script src="http://engine.mock/widget.js" data-key="pk_');

		// The key doesn't list the dashboard: the preview asks, an owner allows it.
		const previewPanel = page.getByRole("region", { name: "Preview" });
		await expect(
			previewPanel.getByRole("heading", { name: `Add ${DASHBOARD_ORIGIN} to this key's sites to preview here` }),
		).toBeVisible();
		await checkPage(page, "widget-preview-origin");
		await previewPanel.getByRole("button", { name: "Allow for previews" }).click();
		await expect(created.getByRole("list", { name: "Allowed sites" })).toContainText("127.0.0.1:5173");
		expect(world.widget.keys.find((k) => k.name === "Shree Labs site")?.allowed_origins).toEqual([
			"https://shree-labs.in",
			DASHBOARD_ORIGIN,
		]);

		// The real widget opens in the frame with the saved look and answers.
		const w = preview(page);
		const panel = w.getByRole("dialog", { name: "Shree Labs help" });
		await expect(panel).toBeVisible();
		await expect(panel.getByText("Namaste! Ask about tests, timings or home collection.")).toBeVisible();
		await expect(panel.getByRole("link", { name: "cob-rag-engine by Callobuzz" })).toBeVisible();
		await expect(w.getByRole("heading", { name: "Gentle dental care for the whole family." })).toBeAttached();
		await page.locator(FRAME).scrollIntoViewIfNeeded();
		await panel.getByRole("textbox", { name: "Your message" }).fill("Home collection in Kothrud on Sunday?");
		await panel.getByRole("textbox", { name: "Your message" }).press("Enter");
		await expect(panel.getByText(WIDGET_ANSWER.replace(" [1]", ""))).toBeVisible();
		await expect(panel.getByText("home-collection.pdf · p.1")).toBeVisible();
		expect(world.widget.chats.at(-1)).toMatchObject({ origin: DASHBOARD_ORIGIN });
		await page.getByRole("radio", { name: "Desktop" }).click();
		// No axe exclusion: widget.js's attribution link is #4b5563 without opacity (engine e238d85).
		await checkPage(page, "widget-preview");

		// Appearance: edits move the sketch; save reloads the live widget.
		const appearance = page.getByRole("region", { name: "Appearance" });
		await appearance.getByRole("radio", { name: "Bottom left" }).click();
		await expect(appearance.getByTestId("mock-bubble")).toHaveAttribute("data-position", "left");
		await appearance.getByLabel("Title").fill("Shree Labs · ask us");
		await appearance.getByRole("button", { name: "Save appearance" }).click();
		await expect(page.getByText("Appearance saved")).toBeVisible();
		// The frame reloads with the saved look (slower under a full parallel run).
		await expect(preview(page).getByRole("dialog", { name: "Shree Labs · ask us" })).toBeVisible({ timeout: 20_000 });

		// Pause: the preview says so and the widget refuses questions.
		await created.getByRole("button", { name: "Pause Shree Labs site" }).click();
		await expect(created.getByText("Paused")).toBeVisible();
		await expect(page.getByTestId("preview-paused")).toContainText("This key is paused");
		const paused = preview(page).getByRole("dialog", { name: "Ask us anything" });
		await page.locator(FRAME).scrollIntoViewIfNeeded();
		await paused.getByRole("textbox", { name: "Your message" }).fill("Are you open today?");
		await paused.getByRole("textbox", { name: "Your message" }).press("Enter");
		await expect(paused.getByText("Invalid or disabled widget key")).toBeVisible();
		await created.getByRole("button", { name: "Resume Shree Labs site" }).click();
		await expect(created.getByText("Active")).toBeVisible();

		// Rotate: plain confirm naming the site; the snippet changes.
		await created.getByRole("button", { name: "Rotate Shree Labs site" }).click();
		const rot = page.getByRole("alertdialog", { name: "Rotate Shree Labs site?" });
		await expect(rot).toContainText("The snippet on shree-labs.in and 127.0.0.1:5173 must be updated");
		await rot.getByRole("button", { name: "Rotate key" }).click();
		await expect(created.getByTestId("snippet")).not.toHaveText(snippetBefore ?? "");
		await created.getByRole("button", { name: "Copy snippet for Shree Labs site" }).click();
		expect(await clipboard(page)).toBe(await created.getByTestId("snippet").textContent());

		// Delete.
		await created.getByRole("button", { name: "Delete Shree Labs site" }).click();
		const del = page.getByRole("alertdialog", { name: "Delete Shree Labs site?" });
		await expect(del).toContainText("The widget on shree-labs.in and 127.0.0.1:5173 stops working at once.");
		await del.getByRole("button", { name: "Delete key" }).click();
		await expect(card(page, "Shree Labs site")).toHaveCount(0);
		expect(world.widget.keys.map((k) => k.name)).toEqual(["Live site"]);
	});

	test("a member sees keys and snippets read-only", async ({ page }, info) => {
		test.skip(roleOf(info) !== "member", "member journey");
		await logIn(page, "member");
		await page.goto(`/widget?kb=${PATIENT}`);
		const live = card(page, "Live site");
		for (const name of ["Edit Live site", "Pause Live site", "Rotate Live site", "Delete Live site"]) {
			await expect(live.getByRole("button", { name })).toHaveAttribute("aria-disabled", "true");
		}
		await expect(page.getByRole("button", { name: "New widget key" })).toHaveAttribute("aria-disabled", "true");
		await live.getByRole("button", { name: "Delete Live site" }).click({ force: true });
		await expect(page.getByText("Only owners can manage widget keys").first()).toBeVisible();
		await expect(page.getByRole("alertdialog")).toHaveCount(0);
		await live.getByRole("button", { name: "Copy snippet for Live site" }).click();
		expect(await clipboard(page)).toContain('data-key="pk_shree_live_8f2a91c4d7e0"');
		await live.getByRole("button", { name: "Preview and style Live site" }).click();
		await expect(page.getByRole("region", { name: "Appearance" }).getByLabel("Title")).toBeDisabled();
		await expect(page.getByRole("button", { name: "Allow for previews" })).toHaveAttribute("aria-disabled", "true");
		await checkPage(page, "widget-member");
	});

	test("plan cap: a second key is refused with widget_key_limit_reached", async ({ page }, info) => {
		test.skip(roleOf(info) !== "owner", "tenant owner with max_widget_keys 1");
		await logIn(page, "owner");
		await page.goto(`/widget?kb=${PATIENT}`);
		await expect(page.getByTestId("plan-note")).toHaveText(/1 of 1 widget keys on your plan/);
		await page.getByRole("button", { name: "New widget key" }).click();
		const dialog = page.getByRole("dialog", { name: "New widget key" });
		await dialog.getByLabel("Name").fill("Second site");
		await dialog.getByLabel("Allowed sites").fill("https://staging.shree-labs.in");
		await dialog.getByLabel("Allowed sites").press("Enter");
		await dialog.getByRole("button", { name: "Create widget key" }).click();
		const alert = dialog.getByRole("alert");
		await expect(alert).toContainText("Your plan allows 1 website widget keys.");
		await expect(alert.getByRole("link", { name: "See plan" })).toBeVisible();
		await expectNoAxeViolations(page, "widget cap");
		await fullShot(page, screenshotPath(info, "widget-cap"));
		expect(world.widget.keys).toHaveLength(1);
	});
});

test.describe("agents (MCP)", () => {
	test.beforeEach(async ({ context }) => {
		world = createWorld();
		await installMockEngine(context, world);
		await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: DASHBOARD_ORIGIN });
	});

	test("copy buttons put each client's config on the clipboard", async ({ page }, info) => {
		await logIn(page, roleOf(info));
		await page.goto(`/agents?kb=${PATIENT}`);
		await expect(page.getByRole("heading", { level: 1, name: "Agents (MCP)" })).toBeVisible();
		await expect(page.getByText("You host it: nothing runs on anyone else's servers.", { exact: false })).toBeVisible();
		await expect(page.getByTestId("engine-url")).toHaveText("http://engine.mock");
		await expect(page.getByTestId("loopback-note")).toHaveCount(0);
		await checkPage(page, "agents");

		// Until cob-rag-mcp is on npm the page offers the local install (git clone, pnpm build, npm link).
		await expect(page.getByTestId("local-install")).toContainText("pnpm install && pnpm build && npm link");
		await page.getByRole("button", { name: "Copy Claude Code config" }).click();
		expect(await clipboard(page)).toBe(
			"claude mcp add cob-rag -e COB_RAG_URL=http://engine.mock -e COB_RAG_API_KEY=ck_your_knowledge_base_key -- cob-rag-mcp start",
		);
		await page.getByLabel("How is cob-rag-mcp installed?").selectOption("npx");
		await page.getByRole("button", { name: "Copy Claude Code config" }).click();
		expect(await clipboard(page)).toContain("-- npx -y cob-rag-mcp start");

		const jsonTab = async (tab: string) => {
			await page.getByRole("tab", { name: tab }).click();
			await page.getByRole("button", { name: `Copy ${tab} config` }).click();
			return JSON.parse(await clipboard(page)) as Record<string, Record<string, Record<string, unknown>>>;
		};
		const desktop = await jsonTab("Claude Desktop");
		expect(desktop.mcpServers?.["cob-rag"]).toEqual({
			command: "npx",
			args: ["-y", "cob-rag-mcp", "start"],
			env: { COB_RAG_URL: "http://engine.mock", COB_RAG_API_KEY: "ck_your_knowledge_base_key" },
		});
		await expect(page.getByText("%APPDATA%\\Claude\\claude_desktop_config.json")).toBeVisible();
		const cursor = await jsonTab("Cursor");
		expect(cursor).toEqual(desktop);
		const vscode = await jsonTab("VS Code");
		expect(vscode.servers?.["cob-rag"]).toMatchObject({ type: "stdio", command: "npx" });
		expect(page.url()).toContain("client=vscode");

		await page.getByRole("tab", { name: "n8n / HTTP" }).click();
		await page.getByRole("button", { name: "Copy n8n / HTTP config" }).click();
		expect(await clipboard(page)).toBe(
			"npm i -g cob-rag-mcp\ncob-rag-mcp start --transport http --host 127.0.0.1 --port 3333 --url http://engine.mock",
		);
		await expect(page.getByText("http://127.0.0.1:3333/mcp")).toBeVisible();
		await expect(page.getByText(`COB_RAG_CORPUS_ID=${PATIENT}`, { exact: false })).toBeVisible();
		const getKey = page.getByRole("link", { name: "Manage keys" });
		if (roleOf(info) === "member") await expect(getKey).toHaveAttribute("aria-disabled", "true");
		else await expect(getKey).toHaveAttribute("href", `/kbs/${PATIENT}?tab=key`);
		await checkPage(page, "agents-n8n");
	});

	test("Connect an agent: a read key named for the client, its real config shown once", async ({ page }, info) => {
		test.skip(roleOf(info) === "member", "members cannot create keys");
		await logIn(page, roleOf(info));
		await page.goto(`/agents?kb=${PATIENT}`);
		const connect = page.getByRole("region", { name: "Connect an agent" });
		await expect(connect.getByRole("radio", { name: /^Read/ })).toBeChecked();
		await connect.getByRole("button", { name: "Create key and show config" }).click();
		const done = page.getByTestId("connected-agent");
		await expect(done).toContainText("Key created: Agent – Claude Code");
		const secret = (await done.locator("code").textContent()) ?? "";
		expect(secret).toMatch(/^ck_.*_mockNewSecret\d+$/);
		expect(world.apiKeys.sent.at(-1)?.body).toEqual({ name: "Agent – Claude Code", permission: "read" });
		await expectNoAxeViolations(page, "connected agent");
		await page.getByRole("button", { name: "Copy Claude Code config" }).click();
		expect(await clipboard(page)).toBe(
			`claude mcp add cob-rag -e COB_RAG_URL=http://engine.mock -e COB_RAG_API_KEY=${secret} -- cob-rag-mcp start`,
		);
		await page.getByRole("button", { name: "I've copied it" }).click();
		await expect(page.getByTestId("connected-agent")).toHaveCount(0);
		await page.getByRole("button", { name: "Copy Claude Code config" }).click();
		expect(await clipboard(page)).toContain("ck_your_knowledge_base_key");
	});

	test("Connect an agent is locked for members", async ({ page }, info) => {
		test.skip(roleOf(info) !== "member", "member gating");
		await logIn(page, "member");
		await page.goto(`/agents?kb=${PATIENT}`);
		await expect(page.getByRole("button", { name: "Create key and show config" })).toHaveAttribute(
			"aria-disabled",
			"true",
		);
	});
});

/**
 * Against the running stack: needs A6 (`widget_key_limit_reached`, `max_widget_keys`) merged, the
 * stack rebuilt, the dashboard origin in the engine's CORS list, and an LLM for the answer
 * (else only the greeting is asserted).
 */
test.describe("website widget on the real engine", () => {
	test.fixme("create, allow preview, ask through widget.js, pause, rotate, delete, member read-only, plan cap (needs A6 + a rebuilt stack)", async () => {});
});
