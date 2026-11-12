import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine, type World } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { fullShot } from "./utils/screenshot";
import { isPhone, logIn, roleOf, screenshotPath } from "./utils/session";

/**
 * D2 Overview, Usage and Plan & billing on the mock engine (e2e/fixtures/mock-overview.ts) across
 * the README matrix: Shree Labs with two knowledge bases, a few questions asked in the
 * playground, two pending sign-ups. The same journeys against the running stack are pending at
 * the bottom of the file.
 */

let world: World;
const PATIENT = "7b221003-0000-4000-8000-000000000001";

async function checkPage(page: Page, name: string) {
	if (isPhone(page)) {
		const width = await page.evaluate(() => document.documentElement.scrollWidth);
		expect(width, name).toBeLessThanOrEqual(390);
	}
	await expect(page.getByTestId("powered-by")).toBeVisible();
	await expectNoAxeViolations(page, name);
	await fullShot(page, screenshotPath(test.info(), name));
}

test.beforeEach(async ({ context }) => {
	world = createWorld();
	world.signUp("Anand Dental Care", "Anand Joshi", "dr.anand@anandental.in");
	// Questions asked in the playground (D5's query state), newest last.
	for (const [i, question] of ["What are your Sunday timings?", "थायरॉईड चाचणीचा दर किती?"].entries()) {
		const id = `0c0c0c0c-0000-4000-8000-00000000000${i + 1}`;
		world.query.queries.set(id, {
			id,
			corpusId: PATIENT,
			question,
			answer: "…",
			model: "openai/gpt-oss-20b",
			latency: 900,
			usage: { prompt_tokens: 1100, completion_tokens: 180, total_tokens: 1280 },
			confidence: 0.9,
		});
	}
	await installMockEngine(context, world);
});

test("overview: KPIs, questions per day and the view's own cards", async ({ page }, info) => {
	const role = roleOf(info);
	// BIZ3: a business lands on Home (see simple-mode.spec.ts); the engine Overview is the operator's
	test.skip(role !== "superadmin", "the operator's Overview");
	await logIn(page, role);
	await expect(page.getByTestId("greeting")).toHaveText(/^Good (morning|afternoon|evening), (SV|Priya|Rohan)$/);
	await expect(page.getByTestId("kpi-questions")).toContainText(/\d/);
	await expect(page.getByTestId("kpi-confidence")).toContainText("% avg");
	await expect(page.getByTestId("kpi-tokens")).toContainText("≈ ₹");
	const chart = page.getByRole("region", { name: "Questions per day" });
	await expect(chart.getByRole("img", { name: /^Questions per day, in the last 7 days: highest/ })).toBeVisible();
	await expect(chart.getByRole("list", { name: "Questions by source" })).toContainText("Website widget");
	await expect(chart.locator(".recharts-bar-rectangle").first()).toBeVisible();

	await expect(page.getByRole("region", { name: "Knowledge gaps" }).getByTestId("gap-row").first()).toContainText(
		"Home collection in Baner on Sunday?",
	);

	await expect(page.getByTestId("overview-sub")).toContainText("2 sign-ups waiting for you.");
	const health = page.getByRole("region", { name: "Service health" });
	await expect(health.getByText("All good")).toBeVisible();
	await expect(health.getByText("OK", { exact: true })).toHaveCount(5);
	await expect(page.getByRole("region", { name: "Top customers" }).getByTestId("top-customer").first()).toContainText(
		"Rangoli Sarees",
	);
	await expect(page.getByRole("region", { name: "Setup checklist" })).toHaveCount(0);
	await checkPage(page, "overview");
	const pending = page.getByRole("region", { name: "2 sign-ups waiting for activation" });
	await expect(pending.getByTestId("pending-signup")).toHaveCount(2);
	await expect(pending).toContainText("Anand Dental Care");
	await pending.getByRole("link", { name: "Review and activate" }).click();
	await expect(page).toHaveURL(/\/admin\/customers\?status=pending$/);
	await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();

	// Range → URL; the table view carries the same numbers.
	await page.goto("/?range=30d");
	await expect(page.getByRole("radio", { name: "30 days" })).toHaveAttribute("aria-checked", "true");
	await chart.getByRole("button", { name: "Show as table" }).click();
	// 30 local days cover 30 or 31 UTC days depending on the time zone and hour (+ the header row).
	await expect(chart.getByRole("table")).toBeVisible();
	const rows = await chart.getByRole("table").getByRole("row").count();
	expect(rows).toBeGreaterThanOrEqual(31);
	expect(rows).toBeLessThanOrEqual(32);
	await expectNoAxeViolations(page, "overview table view");
});

test("usage: filter by knowledge base, CSV totals match the summary, ₹ ↔ $", async ({ page }, info) => {
	const role = roleOf(info);
	await logIn(page, role);
	await page.goto("/usage");
	await expect(page.getByRole("heading", { level: 1, name: "Usage" })).toBeVisible();
	await expect(page.getByRole("table", { name: "Usage by knowledge base" })).toBeVisible();
	if (role === "superadmin") await expect(page.getByLabel("Customer", { exact: true })).toBeVisible();
	else await expect(page.getByLabel("Customer", { exact: true })).toHaveCount(0);

	await page.getByLabel("Knowledge base", { exact: true }).selectOption({ label: "Patient help" });
	await expect(page).toHaveURL(new RegExp(`kb=${PATIENT}`));
	const table = page.getByRole("table", { name: "Usage by knowledge base" });
	await expect(table.locator("tbody tr")).toHaveCount(1);
	const summary = page.getByTestId("usage-summary");
	const questions = (await summary.locator("div.font-display").first().innerText()).replace(/,/g, "");

	const download = page.waitForEvent("download");
	await page.getByRole("button", { name: "Export CSV" }).click();
	const file = await download;
	expect(file.suggestedFilename()).toMatch(/^usage-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2}-corpus\.csv$/);
	const bytes = readFileSync((await file.path()) ?? "");
	expect([...bytes.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
	const lines = bytes.subarray(3).toString("utf8").trimEnd().split("\r\n");
	expect(lines[0]).toBe("key,label,requests,prompt_tokens,completion_tokens,cost_usd,cost_inr,unpriced_requests");
	const totals = (lines.at(-1) ?? "").split(",");
	expect(totals[1]).toBe("Total");
	expect(totals[2]).toBe(questions);

	await expect(summary).toContainText("₹");
	await page.getByRole("radiogroup", { name: "Currency" }).getByRole("radio", { name: "$ USD" }).click();
	await expect(summary).toContainText("$");
	await expect(summary).not.toContainText("₹");
	await page.getByRole("radiogroup", { name: "Group by" }).getByRole("radio", { name: "Source" }).click();
	await expect(page.getByRole("table", { name: "Usage by source" })).toContainText("Widget · Patient help · Live site");
	await checkPage(page, "usage");
	await page.getByRole("radiogroup", { name: "Currency" }).getByRole("radio", { name: "₹ INR" }).click();

	// A custom range over 366 days is refused before any call.
	await page.goto("/usage?range=custom&from=2024-01-01&to=2026-09-30");
	await expect(page.getByRole("alert")).toContainText("Pick at most 366 days.");
});

test("plan & usage: meters match GET /tenants/me/plan; the admin can't open it", async ({ page }, info) => {
	const role = roleOf(info);
	await logIn(page, role);
	if (role === "superadmin") {
		await page.goto("/plan");
		await expect(page.getByRole("heading", { name: "Not available for your account" })).toBeVisible();
		return;
	}
	// The Overview just shown also asks for the plan: under load its response can still be on the
	// way when this listener starts, and its body is gone once /plan replaces that page. Only the
	// /plan page's own response (its frame already at /plan) is read.
	const planResponse = page.waitForResponse(
		(r) => r.url().endsWith("/api/v1/tenants/me/plan") && new URL(r.frame().url()).pathname === "/plan",
	);
	await page.goto("/plan");
	const plan = (await (await planResponse).json()) as {
		plan: { name: string };
		limits: Record<string, number | null>;
		usage: Record<string, number>;
	};
	await expect(page.getByTestId("plan-name")).toHaveText(plan.plan.name);
	const fmt = (n: number) => new Intl.NumberFormat("en-IN").format(n);
	const expectMeter = async (name: string, used: number, limit: number | null) => {
		if (limit == null) return;
		await expect(page.getByRole("meter", { name })).toHaveAttribute(
			"aria-valuetext",
			new RegExp(`^${fmt(used)} of ${fmt(limit)}`),
		);
	};
	await expectMeter("Questions this month", plan.usage.questions ?? 0, plan.limits.monthly_questions ?? null);
	await expectMeter("Knowledge bases", plan.usage.knowledge_bases ?? 0, plan.limits.max_knowledge_bases ?? null);
	await expectMeter("Documents", plan.usage.documents ?? 0, plan.limits.max_documents ?? null);
	await expectMeter("Website widget keys", plan.usage.widget_keys ?? 0, plan.limits.max_widget_keys ?? null);
	await expect(page.getByTestId("plan-meters")).toContainText("resets on");
	await expect(page.getByTestId("plan-meters")).toContainText("Patient help, Staff SOPs");
	await expect(page.getByRole("link", { name: "Email about upgrading" })).toHaveAttribute(
		"href",
		"mailto:support@sahayak.ai?subject=Upgrade%20Shree%20Labs",
	);
	await expect(
		page.getByRole("region", { name: "This month by knowledge base" }).getByTestId("month-kb").first(),
	).toContainText("Patient help");
	await checkPage(page, "plan");
});

/**
 * Against the running stack: needs A0–A8 merged, the stack rebuilt, RAG_SUPERADMIN_EMAIL set and
 * a few questions asked through the playground so `usage_daily` has rows.
 */
test.describe("overview, usage and plan on the real engine", () => {
	test.fixme("admin overview with pending sign-ups and service health, usage CSV totals, owner checklist and plan meters (needs the running stack)", async () => {});
});
