import path from "node:path";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { createLeadsState, installLeadsMock } from "./fixtures/mock-leads"; // OS4: the leads inbox
import { isPhone, logIn } from "./utils/session";

/**
 * @screenshots — the README and docs screenshots (dashboard D12), not part of the default run:
 *
 *   DOCS_SCREENSHOTS=1 pnpm e2e --project docs-screenshots
 *
 * The demo world is the mock engine's (e2e/fixtures/mock-engine.ts: operator "Sahayak AI",
 * tenants Shree Labs, Rangoli Sarees, Vidya Classes, Mehta Opticals; KB "Patient help" with the
 * D4 documents) plus a pending sign-up, "Anand Dental Care", and English and Hindi questions
 * asked through the UI. The dashboard reaches it through its normal API calls (answered by the
 * mock), so the pictures never depend on, or change, a running engine's data. The mock's
 * addresses are shown as a default local install's (engine 127.0.0.1:8000, dashboard
 * 127.0.0.1:8090). Every screen is shot in light and dark at 1440 × 900 and 390 × 844 into
 * docs/assets/screenshots/dashboard-<screen>-<theme>-<width>.png, plus the four
 * README images: widget-desktop.png, widget-mobile.png (the real widget.js on widget-demo.html),
 * dashboard-widget.png and dashboard-usage.png.
 */

const OUT = process.env.DOCS_SCREENSHOTS_DIR ?? path.resolve(import.meta.dirname, "../../../docs/assets/screenshots");
const PATIENT = "7b221003-0000-4000-8000-000000000001";
const ENGINE = "http://engine.mock";

const VARIANTS = [
	{ theme: "light", width: 1440, height: 900 },
	{ theme: "dark", width: 1440, height: 900 },
	{ theme: "light", width: 390, height: 844 },
	{ theme: "dark", width: 390, height: 844 },
] as const;

/** The dental clinic's own widget key for the widget-demo.html shots. */
const DENTAL_KEY = "pk_anand_dental_4c1e9b7a20d3";

const DENTAL_ANSWERS: {
	match: RegExp;
	text: string;
	sources: { file_name: string; section: string; page: number }[];
}[] = [
	{
		match: /root canal|रूट कैनाल/i,
		text: "A root canal at Anand Dental Care costs ₹4,500–₹7,000 per tooth, depending on the tooth; a crown is extra (from ₹6,000). Most treatments finish in two visits, and we accept UPI, cards and cash. [1]",
		sources: [{ file_name: "treatments-and-fees.pdf", section: "Root canal", page: 2 }],
	},
	{
		match: /sunday|रविवार|timing/i,
		text: "We are open Monday to Saturday, 10 am to 8 pm, and on Sunday from 10 am to 1 pm for emergencies only. Book on WhatsApp at +91 98220 12345. [1]",
		sources: [{ file_name: "clinic-timings.md", section: "Timings", page: 1 }],
	},
];

/** Anand Dental Care signed up and verified: pending until the operator activates it. */
function demoWorld(): World {
	const world = createWorld();
	const sai = world.tenants.find((t) => t.name === "Sai Tutorials");
	if (sai) sai.name = "Vidya Classes";
	world.signUp("Anand Dental Care", "Dr. Meera Anand", "meera@ananddental.in");
	// OS4: Plan & billing shows one plan everywhere: the owner's Pro, with the limits its header shows
	const pro = world.planChoice.plans.find((p) => p.name === "Pro");
	if (pro) {
		pro.current = true;
		pro.includes = {
			...pro.includes,
			max_knowledge_bases: 3,
			monthly_questions: 5000,
			max_documents: 500,
			max_widget_keys: 3,
		};
	}
	return world;
}

/** The clinic's own widget key, on its own site only (widget-demo.html shots). */
function addDentalKey(world: World, origin: string) {
	world.widget.keys.push({
		id: "5b000000-0000-4000-8000-0000000000a1",
		corpus_id: PATIENT,
		name: "Anand Dental site",
		public_key: DENTAL_KEY,
		allowed_origins: ["https://ananddental.in", origin],
		rate_limit_per_minute: 10,
		daily_message_limit: 500,
		is_active: true,
		created_at: "2026-09-24T10:00:00Z",
		appearance: {
			title: "Anand Dental Care",
			primary_color: "#0b6e63",
			greeting: "Namaste! Ask about treatments, fees or clinic timings.",
		},
	});
}

/** The dental clinic's answers, streamed like the engine's widget chat (later routes win). */
async function dentalChat(context: BrowserContext) {
	await context.route(`${ENGINE}/api/v1/widget/chat`, async (route) => {
		const req = route.request();
		if (req.method() !== "POST" || req.headers()["x-widget-key"] !== DENTAL_KEY) return route.fallback();
		const message = String((req.postDataJSON() as { message?: string }).message ?? "");
		const answer = DENTAL_ANSWERS.find((a) => a.match.test(message)) ?? DENTAL_ANSWERS[1];
		if (!answer) return route.fallback();
		const ev = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
		const sources = answer.sources.map((s, i) => ({ index: i + 1, ...s, snippet: "" }));
		const tokens = answer.text.match(/.{1,24}/gu) ?? [answer.text];
		await route.fulfill({
			status: 200,
			headers: { "access-control-allow-origin": "*", "content-type": "text/event-stream" },
			body: ev("sources", { sources }) + tokens.map((c) => ev("token", { content: c })).join("") + ev("done", {}),
		});
	});
}

async function settle(page: Page) {
	await page.waitForLoadState("networkidle");
	await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0, { timeout: 15_000 });
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await page.evaluate(() => document.fonts.ready);
}

/**
 * The mock's addresses read as a default local install's in the pictures: the engine on
 * 127.0.0.1:8000 and the dashboard on 127.0.0.1:8090 (text only; every frame of the page).
 */
const LOCAL_ADDRESSES: [string, string][] = [
	[ENGINE, "http://127.0.0.1:8000"],
	["127.0.0.1:5173", "127.0.0.1:8090"],
];

async function shot(page: Page, name: string) {
	for (const frame of page.frames()) {
		await frame.evaluate((pairs) => {
			const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
			for (let n = walker.nextNode(); n; n = walker.nextNode()) {
				let text = n.nodeValue ?? "";
				for (const [from, to] of pairs) text = text.split(from).join(to);
				if (text !== n.nodeValue) n.nodeValue = text;
			}
		}, LOCAL_ADDRESSES);
	}
	await page.screenshot({ path: path.join(OUT, name), animations: "disabled", caret: "hide" });
}

/** A page whose heading text lives in its strings file: wait for its h1, then settle. */
async function anyHeading(page: Page) {
	await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
	await settle(page);
}

async function heading(page: Page, name: string) {
	await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
	await settle(page);
}

for (const v of VARIANTS) {
	const suffix = `${v.theme}-${v.width}`;
	const file = (screen: string) => `dashboard-${screen}-${suffix}.png`;
	/** The README images: light, desktop. */
	const readme = v.theme === "light" && v.width === 1440;

	test.describe(`@screenshots ${suffix}`, () => {
		test.use({ viewport: { width: v.width, height: v.height }, colorScheme: v.theme });

		let world: World;
		test.beforeEach(async ({ context, page }) => {
			world = demoWorld();
			await installMockEngine(context, world);
			await installLeadsMock(context, world, createLeadsState(world));
			await dentalChat(context);
			await page.emulateMedia({ reducedMotion: "reduce" });
		});

		test("super admin screens", async ({ page, baseURL }) => {
			test.setTimeout(180_000);
			const origin = new URL(baseURL ?? "http://127.0.0.1:5173").origin;
			const live = world.widget.keys.find((k) => k.name === "Live site");
			live?.allowed_origins.push(origin);

			await page.goto("/login");
			await expect(page.getByRole("heading", { name: "Log in to Sahayak AI" })).toBeVisible();
			await settle(page);
			await shot(page, file("login"));

			await logIn(page, "superadmin");
			await settle(page);
			await shot(page, file("overview-admin"));

			await page.goto(`/kbs/${PATIENT}?tab=retrieval`);
			await heading(page, "Patient help");
			await shot(page, file("kb-retrieval"));

			await page.goto(`/playground?kb=${PATIENT}`);
			await heading(page, "Playground");
			for (const q of ["Do I need to fast for a lipid profile test?", "क्या लिपिड प्रोफ़ाइल से पहले उपवास ज़रूरी है?"]) {
				await page.getByRole("textbox", { name: "Question" }).fill(q);
				await page.getByRole("button", { name: "Ask", exact: true }).click();
				await expect(page.getByTestId("bot-answer").last()).toHaveAttribute("data-status", "done");
			}
			await settle(page);
			await shot(page, file("playground"));

			await page.goto(`/widget?kb=${PATIENT}`);
			await heading(page, "Website widget");
			await page.getByRole("button", { name: "Preview and style Live site" }).click();
			const frame = page.frameLocator('iframe[title="Widget preview"]');
			const panel = frame.getByRole("dialog", { name: "Shree Labs help" });
			await expect(panel).toBeVisible();
			await panel.getByRole("textbox", { name: "Your message" }).fill("Home collection in Kothrud on Sunday?");
			await panel.getByRole("textbox", { name: "Your message" }).press("Enter");
			await expect(panel.getByText("home-collection.pdf · p.1")).toBeVisible();
			if (!isPhone(page)) await page.getByRole("radio", { name: "Desktop" }).click();
			await settle(page);
			if (isPhone(page)) await page.locator('iframe[title="Widget preview"]').scrollIntoViewIfNeeded();
			await shot(page, file("widget"));
			if (readme) await shot(page, "dashboard-widget.png");

			await page.goto("/admin/customers?status=pending");
			await heading(page, "Customers");
			await expect(page.getByText("Anand Dental Care").first()).toBeVisible();
			await shot(page, file("customers-pending"));

			await page.goto("/admin/plans");
			await heading(page, "Plans");
			await shot(page, file("plans"));

			await page.goto("/admin/branding");
			await heading(page, "Sign-up & branding");
			await shot(page, file("branding"));

			// OS4: the screens added since the first set (billing, the public website editor)
			await page.goto("/admin/billing");
			await anyHeading(page);
			await shot(page, file("billing-admin"));

			await page.goto("/admin/landing");
			await anyHeading(page);
			await shot(page, file("landing-editor"));

			if (readme) {
				await page.goto("/usage");
				await heading(page, "Usage");
				await shot(page, "dashboard-usage.png");
			}
		});

		test("owner screens", async ({ page }) => {
			test.setTimeout(120_000);
			await logIn(page, "owner");
			await settle(page);
			await shot(page, file("overview-owner"));

			await page.goto("/team");
			await heading(page, "Team");
			await shot(page, file("team"));

			await page.goto("/plan");
			await heading(page, "Plan & billing");
			await shot(page, file("plan-usage"));

			await page.goto("/leads");
			await anyHeading(page);
			await shot(page, file("leads"));
		});

		if (v.theme === "light") {
			test("the widget on a customer's site", async ({ page, baseURL }) => {
				const origin = new URL(baseURL ?? "http://127.0.0.1:5173").origin;
				addDentalKey(world, origin);
				await page.goto(`/widget-demo.html?${new URLSearchParams({ key: DENTAL_KEY, api: ENGINE })}`);
				const panel = page.getByRole("dialog", { name: "Anand Dental Care" });
				await expect(panel).toBeVisible();
				const questions = isPhone(page)
					? ["Root canal ka kharcha kitna hai?", "Are you open on Sunday?"]
					: ["How much does a root canal cost?"];
				for (const q of questions) {
					await panel.getByRole("textbox", { name: "Your message" }).fill(q);
					await panel.getByRole("textbox", { name: "Your message" }).press("Enter");
					await expect(panel.getByText(/clinic-timings\.md|treatments-and-fees\.pdf/).last()).toBeVisible();
				}
				await expect(
					panel.getByText(questions.length === 2 ? "clinic-timings.md · p.1" : "treatments-and-fees.pdf · p.2"),
				).toBeVisible();
				await page.waitForLoadState("networkidle");
				await shot(page, isPhone(page) ? "widget-mobile.png" : "widget-desktop.png");
			});
		}
	});
}
