import { defineConfig, devices } from "@playwright/test";

/**
 * Projects (README "Tests"): superadmin-light-1440, superadmin-dark-390, owner-light-390,
 * owner-dark-1440 (tenant view), member-light-1440 (gating). The role is read from
 * `project.metadata.role`; the theme follows `colorScheme` (the dashboard default is "system").
 *
 * - shell, kbs, content, playground, widget-agents, customers, team, profile, overview-usage, crawler,
 *   insights-quality, admin-engine, portal and auth-flows specs: the mock engine (e2e/fixtures/mock-engine.ts), runnable now.
 * - live.spec.ts: the running engine at 127.0.0.1:8000 (public branding, key sign-in).
 * - real-stack.spec.ts: the whole customer journey on the real engine; skipped unless E2E_ACCOUNTS=1.
 * - auth.spec.ts: the real accounts stack (A3–A5 merged); skipped unless E2E_ACCOUNTS=1.
 * - screenshots.spec.ts (@screenshots): the README/docs images, only with DOCS_SCREENSHOTS=1.
 * - smoke-image.spec.ts: the built image (D12), only with E2E_IMAGE_URL; no dev server then.
 *
 * The whole mock matrix also runs against the image: start it on the dev origin with the mock
 * engine's URLs, and Playwright reuses it instead of starting `pnpm dev`:
 *   docker run --rm -d -p 127.0.0.1:5173:8080 -e RAG_DASHBOARD_ENGINE_URL=http://engine.mock  *     -e RAG_DASHBOARD_SCRAPER_URL=http://scraper.mock cob-rag-dashboard:local
 */
const desktop = { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } };
const phone = { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, isMobile: false, hasTouch: true };

const matrix = [
	{
		name: "superadmin-light-1440",
		use: { ...desktop, colorScheme: "light" as const },
		metadata: { role: "superadmin" },
	},
	{ name: "superadmin-dark-390", use: { ...phone, colorScheme: "dark" as const }, metadata: { role: "superadmin" } },
	{ name: "owner-light-390", use: { ...phone, colorScheme: "light" as const }, metadata: { role: "owner" } },
	{ name: "owner-dark-1440", use: { ...desktop, colorScheme: "dark" as const }, metadata: { role: "owner" } },
	{ name: "member-light-1440", use: { ...desktop, colorScheme: "light" as const }, metadata: { role: "member" } },
];

const imageUrl = process.env.E2E_IMAGE_URL;

// LP2 landing: the public website's specs (e2e/landing) run on snapshots rendered by the engine's
// own renderer and served by e2e/landing/serve.mjs, so neither the dashboard dev server nor the
// stack is started for them. `pnpm e2e:landing` (or any path under e2e/landing) switches that on.
const landingRun = process.argv.some((arg) => arg.replaceAll("\\", "/").includes("e2e/landing"));
// A worker re-reads this file with its own argv; it must still know the landing projects.
const inWorker = process.env.TEST_WORKER_INDEX !== undefined;
const landingMatch = /e2e[\\/]landing[\\/].*\.spec\.ts/;
const landingProjects = [
	{ name: "landing-light-1440", use: { ...desktop, colorScheme: "light" as const }, testMatch: landingMatch },
	{ name: "landing-dark-1440", use: { ...desktop, colorScheme: "dark" as const }, testMatch: landingMatch },
	{ name: "landing-light-390", use: { ...phone, colorScheme: "light" as const }, testMatch: landingMatch },
	{ name: "landing-dark-390", use: { ...phone, colorScheme: "dark" as const }, testMatch: landingMatch },
];
// /LP2 landing

export default defineConfig({
	testDir: "e2e",
	fullyParallel: true,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	reporter: [["list"]],
	timeout: 45_000,
	globalSetup: landingRun ? "./e2e/landing/setup.ts" : undefined, // LP2 landing
	use: {
		baseURL: "http://127.0.0.1:5173",
		trace: "retain-on-failure",
	},
	projects: [
		...(landingRun || inWorker ? landingProjects : []), // LP2 landing
		...matrix.map((p) => ({
			...p,
			testMatch:
				/(shell|auth|auth-brand|kbs|content|playground|widget-agents|customers|team|profile|overview-usage|provider-balances|billing|billing-banner|billing-statements|crawler|insights-quality|admin-engine|portal|layout-scroll|operator-workspace|assistant|assistant-setup|review-outcomes|leads|benchmarks|cob|cob-public|docs|simple-mode|website|onboarding|pipelines|value|support|whatsapp|no-surprises|legal|payments|invoices|shop|plan-choice|landing-editor)\.spec\.ts/, // P3 legal, P1 payments, P2 invoices, P5 plans, P6 plan choice, LP4 landing editor
		})),
		{ name: "auth-flows", use: { ...desktop, colorScheme: "light" }, testMatch: /auth-flows\.spec\.ts/ },
		{
			name: "real-stack",
			use: { ...desktop, colorScheme: "light" },
			testMatch: /real-stack.spec.ts/,
			timeout: 120_000,
		},
		{ name: "live-engine", use: { ...desktop, colorScheme: "dark" }, testMatch: /live\.spec\.ts/ },
		...(process.env.DOCS_SCREENSHOTS === "1"
			? [{ name: "docs-screenshots", use: { ...desktop }, testMatch: /screenshots\.spec\.ts/, grep: /@screenshots/ }]
			: []),
		...(imageUrl
			? [
					{
						name: "image-smoke",
						use: { ...desktop, colorScheme: "light" as const },
						testMatch: /smoke-image\.spec\.ts/,
					},
				]
			: []),
	],
	// Against the image (E2E_IMAGE_URL) there is nothing to start.
	webServer:
		imageUrl || landingRun // LP2 landing: the landing specs serve their own snapshots
			? undefined
			: {
					command: "pnpm dev",
					url: "http://127.0.0.1:5173",
					reuseExistingServer: true,
					timeout: 120_000,
				},
});
