import { expect, type Page, test } from "@playwright/test";
import { expectNoAxeViolations } from "./utils/axe";

/**
 * The built image (dashboard D12) against a running stack, not part of the default run:
 *
 *   docker compose up -d --build dashboard app scraper
 *   E2E_IMAGE_URL=http://127.0.0.1:8090 pnpm e2e --project image-smoke
 *
 * - E2E_IMAGE_URL: where the image is served (default http://127.0.0.1:8090)
 * - E2E_IMAGE_ENGINE_URL / E2E_IMAGE_SCRAPER_URL: the RAG_DASHBOARD_* values the container was
 *   started with (defaults http://127.0.0.1:8000 and http://127.0.0.1:8001)
 * - E2E_ADMIN_KEY (optional, never committed): also signs in with the admin key.
 */
const IMAGE = (process.env.E2E_IMAGE_URL ?? "http://127.0.0.1:8090").replace(/\/+$/, "");
const ENGINE = (process.env.E2E_IMAGE_ENGINE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const SCRAPER = (process.env.E2E_IMAGE_SCRAPER_URL ?? "http://127.0.0.1:8001").replace(/\/+$/, "");
const ADMIN_KEY = process.env.E2E_ADMIN_KEY ?? "";

/** Every CSP violation the page reports (the event fires for blocked requests and scripts). */
async function recordViolations(page: Page): Promise<() => Promise<string[]>> {
	await page.addInitScript(() => {
		const list: string[] = [];
		(window as unknown as { __csp: string[] }).__csp = list;
		document.addEventListener("securitypolicyviolation", (e) => {
			list.push(`${e.violatedDirective} ${e.blockedURI}`);
		});
	});
	const fromConsole: string[] = [];
	page.on("console", (m) => {
		if (m.type() === "error" && m.text().includes("Content Security Policy")) fromConsole.push(m.text());
	});
	return async () => [
		...fromConsole,
		...(await page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? [])),
	];
}

test.describe("image over HTTP", () => {
	test("/ serves the app with the security and cache headers", async ({ request }) => {
		const res = await request.get(`${IMAGE}/`);
		expect(res.status()).toBe(200);
		expect(res.headers()["content-type"]).toContain("text/html");
		const html = await res.text();
		expect(html).toContain('<div id="root"></div>');
		expect(html).toContain('<script src="/config.js"></script>');
		const h = res.headers();
		expect(h["cache-control"]).toBe("no-cache");
		expect(h["x-content-type-options"]).toBe("nosniff");
		expect(h["referrer-policy"]).toBe("strict-origin-when-cross-origin");
		expect(h["permissions-policy"]).toBe("camera=(), microphone=(), geolocation=()");
		const csp = h["content-security-policy"] ?? "";
		expect(csp).toContain(`connect-src 'self' ${new URL(ENGINE).origin} ${new URL(SCRAPER).origin};`);
		expect(csp).toMatch(/script-src 'self';/);
		expect(csp.match(/script-src[^;]*/)?.[0]).not.toContain("unsafe-inline");
		expect(csp).toContain("frame-ancestors 'self'");
		expect(csp).toContain("object-src 'none'");
	});

	test("a deep link serves index.html (SPA fallback)", async ({ request }) => {
		const res = await request.get(`${IMAGE}/admin/customers?status=pending`);
		expect(res.status()).toBe(200);
		expect(res.headers()["content-type"]).toContain("text/html");
		expect(await res.text()).toContain('<div id="root"></div>');
	});

	test("/config.js carries the container's values in the RAG_CONFIG shape", async ({ request }) => {
		const res = await request.get(`${IMAGE}/config.js`);
		expect(res.status()).toBe(200);
		expect(res.headers()["cache-control"]).toBe("no-cache");
		const js = await res.text();
		expect(js).toContain("globalThis.RAG_CONFIG = {");
		expect(js).toContain(`ragUrl: "${ENGINE}",`);
		expect(js).toContain(`scraperUrl: "${SCRAPER}",`);
		expect(js).toMatch(/usdToInr: \d+(\.\d+)?,/);
		expect((await request.get(`${IMAGE}/config.js.template`)).status()).toBe(404);
	});

	test("/healthz answers ok", async ({ request }) => {
		const res = await request.get(`${IMAGE}/healthz`);
		expect(res.status()).toBe(200);
		expect(await res.text()).toBe("ok");
	});

	test("hashed assets are immutable; a missing one is a 404, not index.html", async ({ request }) => {
		const html = await (await request.get(`${IMAGE}/`)).text();
		const asset = html.match(/\/assets\/[^"]+\.js/)?.[0];
		expect(asset).toBeTruthy();
		const res = await request.get(`${IMAGE}${asset}`);
		expect(res.status()).toBe(200);
		expect(res.headers()["cache-control"]).toBe("public, max-age=31536000, immutable");
		expect(res.headers()["content-type"]).toContain("javascript");
		expect((await request.get(`${IMAGE}/assets/not-a-chunk.js`)).status()).toBe(404);
	});

	test("/widget-demo.html may load scripts from the engine only", async ({ request }) => {
		const res = await request.get(`${IMAGE}/widget-demo.html`);
		expect(res.status()).toBe(200);
		const csp = res.headers()["content-security-policy"] ?? "";
		const engine = new URL(ENGINE).origin;
		expect(csp).toContain(`script-src 'self' ${engine};`);
		expect(csp).toContain(`connect-src ${engine};`);
	});
});

test.describe("image in the browser", () => {
	test("the login page loads branding from the engine without CSP violations", async ({ page, request }) => {
		const violations = await recordViolations(page);
		const branding = await (await request.get(`${ENGINE}/api/v1/public/branding`)).json();
		await page.goto(`${IMAGE}/login`);
		await expect(
			page.getByRole("complementary").getByText(branding.product_name, { exact: true }).first(),
		).toBeVisible();
		await expect(page.getByText("Couldn't load this engine's branding")).toHaveCount(0);
		await expect(page.getByTestId("powered-by")).toBeVisible();
		await expect(page).toHaveTitle(new RegExp(`${branding.product_name}$`));
		await expectNoAxeViolations(page, "image login");
		expect(await violations()).toEqual([]);
	});

	test("admin key sign-in, engine and scraper calls, and a reload on a deep link", async ({ page }) => {
		test.skip(!ADMIN_KEY, "E2E_ADMIN_KEY not set");
		const violations = await recordViolations(page);
		const scraperCalls: number[] = [];
		page.on("response", (r) => {
			if (r.url().startsWith(SCRAPER)) scraperCalls.push(r.status());
		});
		await page.goto(`${IMAGE}/login/key`);
		await page.getByLabel("API key").fill(ADMIN_KEY);
		await page.getByRole("button", { name: "Sign in" }).click();
		await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
		await page.waitForLoadState("networkidle");
		await expectNoAxeViolations(page, "image overview");

		await page.goto(`${IMAGE}/crawler`);
		await expect(page.getByRole("heading", { level: 1, name: "Crawler" })).toBeVisible();
		await expect.poll(() => scraperCalls.length).toBeGreaterThan(0);
		expect(scraperCalls.every((s) => s < 400)).toBe(true);

		await page.goto(`${IMAGE}/admin/customers?status=pending`);
		await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
		await page.reload();
		await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
		expect(new URL(page.url()).search).toBe("?status=pending");
		expect(await violations()).toEqual([]);
	});

	test("widget-demo.html loads the engine's widget.js (the CSP allows it)", async ({ page }) => {
		const violations = await recordViolations(page);
		const widget = page.waitForResponse((r) => r.url() === `${ENGINE}/widget.js`);
		await page.goto(`${IMAGE}/widget-demo.html?key=pk_image_smoke_not_a_key&api=${encodeURIComponent(ENGINE)}`);
		expect((await widget).status()).toBe(200);
		await page.waitForLoadState("networkidle");
		expect(await violations()).toEqual([]);
	});
});
