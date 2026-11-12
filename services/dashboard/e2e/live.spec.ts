import { type BrowserContext, expect, test } from "@playwright/test";
import { expectNoAxeViolations } from "./utils/axe";
import { screenshotPath } from "./utils/session";

/**
 * Against the RUNNING engine (http://127.0.0.1:8000, `docker compose up`): public branding and
 * the API-key sign-in, which exist before A3–A5. Requests go to the real engine; the test only
 * adds CORS headers when the local .env's RAG_CORS_ORIGINS does not list the Vite origin yet
 * (set E2E_ENGINE_CORS=1 once it does, and the shim is skipped).
 *
 * E2E_ADMIN_KEY (optional, never committed): also signs in with the admin key.
 */
const ENGINE = process.env.E2E_ENGINE_URL ?? "http://127.0.0.1:8000";

async function corsShim(context: BrowserContext) {
	if (process.env.E2E_ENGINE_CORS === "1") return;
	const cors = {
		"access-control-allow-origin": "http://127.0.0.1:5173",
		"access-control-allow-headers": "authorization, x-api-key, content-type",
		"access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
		"access-control-expose-headers": "retry-after",
	};
	await context.route(`${ENGINE}/**`, async (route) => {
		if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
		const response = await route.fetch();
		await route.fulfill({ response, headers: { ...response.headers(), ...cors } });
	});
}

test.beforeEach(async ({ context, request }) => {
	const health = await request.get(`${ENGINE}/api/v1/public/branding`).catch(() => null);
	test.skip(!health?.ok(), `engine not reachable at ${ENGINE}`);
	await corsShim(context);
});

test("public branding from the engine drives the sign-in page", async ({ page, request }, info) => {
	const branding = await (await request.get(`${ENGINE}/api/v1/public/branding`)).json();
	await page.goto("/login");
	// Engines before A3 have no accounts_enabled: the key sign-in is the way in.
	const expectKeyPage = branding.accounts_enabled !== true;
	await expect(
		page.getByRole("heading", {
			name: expectKeyPage ? "Sign in with an API key" : `Log in to ${branding.product_name}`,
		}),
	).toBeVisible();
	await expect(page.getByText("Couldn't load this engine's branding")).toHaveCount(0);
	await expect(page.getByRole("complementary").getByText(branding.product_name, { exact: true }).first()).toBeVisible();
	const powered = page.getByRole("link", { name: branding.powered_by.text });
	await expect(powered).toHaveAttribute("href", branding.powered_by.url);
	await expect(page).toHaveTitle(new RegExp(`${branding.product_name}$`));
	await expectNoAxeViolations(page, "live sign-in");
	await page.screenshot({ path: screenshotPath(info, "live-key-signin"), fullPage: true });
});

test("a wrong key is refused by the engine", async ({ page }) => {
	await page.goto("/login/key");
	await page.getByLabel("API key").fill("tk_this_key_does_not_exist_0000");
	await page.getByRole("button", { name: "Sign in" }).click();
	await expect(page.getByRole("alert")).toContainText("That key was not accepted.");
	expect(await page.evaluate(() => sessionStorage.getItem("cob.key"))).toBeNull();
});

test("the admin key opens the admin view (needs E2E_ADMIN_KEY)", async ({ page }, info) => {
	const key = process.env.E2E_ADMIN_KEY;
	test.skip(!key, "E2E_ADMIN_KEY not set");
	await page.goto("/login/key");
	await page.getByLabel("API key").fill(key ?? "");
	await page.getByRole("button", { name: "Sign in" }).click();
	await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
	await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Customers" })).toBeVisible();
	await expectNoAxeViolations(page, "live overview");
	await page.screenshot({ path: screenshotPath(info, "live-admin-overview") });
});
