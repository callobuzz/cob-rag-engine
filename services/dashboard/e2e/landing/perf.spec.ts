// LP2: speed on a slow phone. Chromium only (CDP): 150 ms round trip, 1.6 Mbps down, 750 kbps up
// and a CPU four times slower, at 390 px. The headline must be the largest thing painted (also with
// a background video behind the hero), nothing may shift once the conversation has played, and the
// scripts needed before any click must stay small.
import { gzipSync } from "node:zlib";
import { expect, type Page, test } from "@playwright/test";
import { BRANDS, snapName } from "./snapshot";

const BASE = "http://127.0.0.1:4310";

type Lcp = { time: number; tag: string; cls: string };

async function throttle(page: Page): Promise<void> {
	const cdp = await page.context().newCDPSession(page);
	await cdp.send("Network.enable");
	await cdp.send("Network.emulateNetworkConditions", {
		offline: false,
		latency: 150,
		downloadThroughput: (1.6 * 1024 * 1024) / 8,
		uploadThroughput: (750 * 1024) / 8,
	});
	await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
}

async function watch(page: Page): Promise<void> {
	await page.addInitScript(() => {
		const w = window as unknown as { __lcp?: Lcp; __cls?: number };
		w.__cls = 0;
		new PerformanceObserver((list) => {
			for (const entry of list.getEntries()) {
				const el = (entry as PerformanceEntry & { element?: Element | null }).element;
				w.__lcp = { time: entry.startTime, tag: el?.tagName ?? "", cls: el?.className?.toString() ?? "" };
			}
		}).observe({ type: "largest-contentful-paint", buffered: true });
		new PerformanceObserver((list) => {
			for (const entry of list.getEntries()) {
				const shift = entry as PerformanceEntry & { value: number; hadRecentInput: boolean };
				if (!shift.hadRecentInput) w.__cls = (w.__cls ?? 0) + shift.value;
			}
		}).observe({ type: "layout-shift", buffered: true });
	});
}

for (const [label, extra] of [
	["plain hero", ""],
	["hero with a background video", "backdrop"],
] as const) {
	test(`LCP is the headline and CLS stays small: ${label}`, async ({ page, browserName }) => {
		test.skip(browserName !== "chromium", "CDP throttling needs Chromium");
		test.skip((page.viewportSize()?.width ?? 0) > 500, "measured at phone width");
		test.setTimeout(90_000);
		await watch(page);
		await throttle(page);
		await page.goto(`${BASE}/${snapName(BRANDS[0], "services", "en", extra)}/`, { waitUntil: "load" });
		await page.waitForTimeout(6500); // the conversation plays and the backdrop loads
		const lcp = await page.evaluate(() => (window as unknown as { __lcp?: Lcp }).__lcp);
		const cls = await page.evaluate(() => (window as unknown as { __cls: number }).__cls);
		expect(lcp, "an LCP entry").toBeTruthy();
		expect(lcp?.tag).toBe("H1");
		expect(lcp?.time).toBeLessThan(2500);
		expect(cls).toBeLessThan(0.05);
		if (extra === "backdrop") {
			// Nothing of the video is loaded until the visitor moves; then the poster arrives.
			const video = page.locator("video[data-backdrop]");
			await expect(video).not.toHaveAttribute("poster", /.*/);
			await page.mouse.move(40, 200);
			await expect(video).toHaveAttribute("poster", /.webp$/, { timeout: 5000 });
		}
	});
}

test("the scripts requested before any click are 15 KB gzipped or less", async ({ page }) => {
	test.skip((page.viewportSize()?.width ?? 0) > 500, "measured at phone width");
	let bytes = 0;
	const pending: Promise<void>[] = [];
	page.on("response", (response) => {
		if (!response.url().endsWith(".js")) return;
		pending.push(
			response
				.body()
				.then((body) => {
					bytes += gzipSync(body).length;
				})
				.catch(() => undefined),
		);
	});
	await page.goto(`${BASE}/${snapName(BRANDS[0], "services", "en")}/`, { waitUntil: "load" });
	await page.waitForTimeout(800);
	await Promise.all(pending);
	expect(bytes).toBeGreaterThan(0);
	expect(bytes).toBeLessThanOrEqual(15 * 1024);
});
