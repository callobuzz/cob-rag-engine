// LP2: the home page in a real browser, on snapshots rendered by the engine's own renderer.
// Four projects (light and dark, 1440 and 390 wide) run every test; screenshots for human
// review go to LANDING_SHOTS_DIR (see shots.ts), never the repository.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { shoot } from "./shots";
import { BRANDS, snapName } from "./snapshot";

const BASE = "http://127.0.0.1:4310";

function url(brand: string, preset: string, lang: string, extra = ""): string {
	const name = snapName(brand, preset, lang, extra);
	return `${BASE}/${name}/${lang === "en" ? "" : `${lang.toLowerCase()}/`}`;
}

const HOME = url(BRANDS[0], "services", "en");

function isPhone(page: Page): boolean {
	return (page.viewportSize()?.width ?? 1440) < 500;
}

async function settle(page: Page): Promise<void> {
	await page.waitForTimeout(4600); // the hero conversation takes at most four seconds
}

test("the first screen on a phone has the headline, the button and the start of the chat", async ({ page }) => {
	test.skip(!isPhone(page), "a phone screen question");
	await page.goto(HOME);
	const h1 = page.locator("h1");
	await expect(h1).toHaveCount(1);
	await expect(h1).toBeInViewport();
	await expect(page.locator(".hero .btn-primary").first()).toBeInViewport();
	await expect(page.locator(".hero .msg").first()).toBeInViewport();
});

for (const [preset, lang] of [
	["services", "en"],
	["clinic", "hi-Latn"],
] as const) {
	test(`no horizontal scroll: ${preset} in ${lang}`, async ({ page }) => {
		await page.goto(url(BRANDS[0], preset, lang));
		await settle(page);
		const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
		expect(overflow).toBeLessThanOrEqual(0);
	});
}

test("one h1, headings in order, and nothing is drawn on a canvas", async ({ page }) => {
	await page.goto(HOME);
	const levels = await page.$$eval("h1, h2, h3", (els) => els.map((e) => Number(e.tagName[1])));
	expect(levels.filter((l) => l === 1)).toHaveLength(1);
	expect(levels[0]).toBe(1);
	for (let i = 1; i < levels.length; i++) expect((levels[i] ?? 0) - (levels[i - 1] ?? 0)).toBeLessThanOrEqual(1);
	await expect(page.locator("canvas")).toHaveCount(0);
});

test("axe finds nothing serious or critical", async ({ page }) => {
	await page.goto(HOME);
	await settle(page);
	// Bring every scroll-driven element to its final state before it is measured.
	await page.evaluate(async () => {
		for (let y = 0; y < document.documentElement.scrollHeight; y += 400) {
			window.scrollTo(0, y);
			await new Promise((done) => setTimeout(done, 40));
		}
		window.scrollTo(0, 0);
	});
	await page.waitForTimeout(400);
	const result = await new AxeBuilder({ page }).analyze();
	const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
	expect(
		bad.map(
			(v) =>
				`${v.id}: ${v.nodes
					.map((n) => n.target.join(" "))
					.slice(0, 4)
					.join(" | ")}`,
		),
	).toEqual([]);
});

test("without JavaScript every section and the whole conversation are there", async ({ browser, page }, info) => {
	const context = await browser.newContext({
		javaScriptEnabled: false,
		viewport: page.viewportSize() ?? undefined,
		colorScheme: info.project.use.colorScheme ?? "light",
	});
	const bare = await context.newPage();
	await bare.goto(HOME);
	const sections = await bare.$$eval("[data-section]", (els) => els.map((e) => e.getAttribute("data-section")));
	expect(sections.length).toBeGreaterThanOrEqual(12);
	for (const kind of sections) {
		if (kind === "trust_strip" || kind === "pricing_teaser") continue;
		const heading = bare.locator(`[data-section="${kind}"] h1, [data-section="${kind}"] h2`).first();
		await expect(heading, kind ?? "section").toBeVisible();
	}
	const hidden = await bare.$$eval(
		".hero .msg, .hero [data-lead]",
		(els) =>
			els.filter((e) => {
				const style = getComputedStyle(e);
				return style.opacity !== "1" || style.visibility !== "visible";
			}).length,
	);
	expect(hidden).toBe(0);
	await context.close();
});

test("with reduced motion the hero is finished at once and nothing is animating", async ({ browser, page }, info) => {
	const context = await browser.newContext({
		reducedMotion: "reduce",
		viewport: page.viewportSize() ?? undefined,
		colorScheme: info.project.use.colorScheme ?? "light",
	});
	const calm = await context.newPage();
	await calm.goto(HOME);
	await expect(calm.locator("html")).not.toHaveClass(/motion-ok/);
	const pending = await calm.$$eval(".msg.is-pending, [data-lead].is-pending", (els) => els.length);
	expect(pending).toBe(0);
	const running = await calm.evaluate(() => document.getAnimations().filter((a) => a.playState === "running").length);
	expect(running).toBe(0);
	await expect(calm.locator(".site-card .msg").last()).toBeVisible();
	await context.close();
});

test("with motion the hero replays and ends with every message and the lead card shown", async ({ page }) => {
	await page.goto(HOME);
	await expect(page.locator("html")).toHaveClass(/motion-ok/);
	await page.waitForTimeout(500);
	await settle(page);
	expect(await page.$$eval(".msg.is-pending, [data-lead].is-pending", (els) => els.length)).toBe(0);
	await expect(page.locator(".hero [data-lead]")).toBeVisible();
	const text = await page.locator(".site-card .chat").innerText();
	expect(text).toContain("GST filing ka monthly charge kitna hai?");
});

test("a keyboard can walk the whole page into the footer", async ({ page }) => {
	await page.goto(HOME);
	let reached = false;
	for (let i = 0; i < 160 && !reached; i++) {
		await page.keyboard.press("Tab");
		reached = await page.evaluate(() => !!document.activeElement?.closest("footer"));
	}
	expect(reached).toBe(true);
});

test("the narrator sits beside its section on a wide screen and above it on a phone", async ({ page }) => {
	await page.goto(HOME);
	const narrator = page.locator(".beat > .narrator").first();
	const position = await narrator.evaluate((el) => getComputedStyle(el).position);
	expect(position).toBe(isPhone(page) ? "static" : "sticky");
	const order = await page.$$eval(".beat", (beats) =>
		beats.every((b) => b.firstElementChild?.matches(".narrator") && b.lastElementChild?.matches("section")),
	);
	expect(order).toBe(true);
});

test("the sticky bar on a phone appears only after the hero", async ({ page }) => {
	test.skip(!isPhone(page), "the bar is for phones");
	await page.goto(HOME);
	const bar = page.locator("[data-mobile-bar]");
	await expect(bar).not.toHaveClass(/is-visible/);
	await page.evaluate(() => window.scrollTo(0, window.innerHeight * 2));
	await expect(bar).toHaveClass(/is-visible/);
});

test("Ask it yourself requests the widget only on a click, once", async ({ page }) => {
	const requests: string[] = [];
	page.on("request", (request) => {
		if (request.url().endsWith("/widget.js")) requests.push(request.url());
	});
	await page.goto(url(BRANDS[0], "services", "en", "ask"));
	await settle(page);
	expect(requests).toHaveLength(0);
	const button = page.locator("button[data-ask]");
	await button.click();
	await page.waitForTimeout(300);
	await button.click();
	await page.waitForTimeout(300);
	expect(requests).toHaveLength(1);
	const script = page.locator('script[src="/widget.js"]');
	await expect(script).toHaveCount(1);
	await expect(script).toHaveAttribute("data-key", "pk_demo_landing_key");
	await expect(script).toHaveAttribute("data-open", "true");
	await expect(script).toHaveAttribute("data-ask", "GST filing ka monthly charge kitna hai?");
});

test("a page that sells no WhatsApp tells the same story and never names it", async ({ page }) => {
	await page.goto(url(BRANDS[0], "services", "en", "nowa"));
	await expect(page.locator('[data-section="whatsapp_journey"]')).toHaveAttribute("data-variant", "email_handoff");
	await expect(page.locator(".phone")).toHaveCount(0);
	const text = await page.$$eval("main [data-section]", (els) =>
		els
			.map((e) => (e as HTMLElement).innerText)
			.join("\n")
			.replace(/Talk to us on WhatsApp|Or talk to us on WhatsApp/g, ""),
	);
	expect(text.toLowerCase()).not.toContain("whatsapp");
});

test("screenshots for review: three brand colours, two industries, two languages", async ({ page }, info) => {
	test.setTimeout(120_000);
	const combos: Array<[string, string, string]> = [
		[BRANDS[0], "services", "en"],
		[BRANDS[1], "services", "en"],
		[BRANDS[2], "services", "en"],
		[BRANDS[0], "clinic", "hi-Latn"],
	];
	for (const [brand, preset, lang] of combos) {
		await page.goto(url(brand, preset, lang));
		await settle(page);
		await page.evaluate(async () => {
			for (let y = 0; y < document.documentElement.scrollHeight; y += 450) {
				window.scrollTo(0, y);
				await new Promise((done) => setTimeout(done, 50));
			}
			window.scrollTo(0, 0);
		});
		await page.waitForTimeout(500);
		const files = await shoot(page, info, `home-${brand.slice(1)}-${preset}-${lang}`);
		expect(files).toHaveLength(2);
	}
});
