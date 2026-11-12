import { expect, type Page, test } from "@playwright/test";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { isPhone, logIn, openAdvanced, roleOf } from "./utils/session";

/**
 * One scroll region (app-shell pattern). Signed in, the document never scrolls: the sidebar, the
 * top bar and the shell stay one viewport tall and only `#content` scrolls, with the
 * "cob-rag-engine by Callobuzz" footer at the end of it. Public pages scroll as a normal document
 * with no nested scroller and no gap under the content. Runs on the 1440x900 and 390x844 projects.
 */

/** Make the page taller than any viewport so the scroll region definitely has to scroll. */
async function addTallContent(page: Page, selector: string) {
	await page.evaluate((sel) => {
		const filler = document.createElement("div");
		filler.setAttribute("data-testid", "tall-filler");
		filler.style.cssText = "height:2400px;flex:none";
		document.querySelector(sel)?.appendChild(filler);
	}, selector);
}

test.beforeEach(async ({ context }) => {
	await installMockEngine(context, createWorld());
});

test("signed in: only #content scrolls; sidebar, top bar and footer stay reachable", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await addTallContent(page, "#main");
	const content = page.locator("#content");
	await expect(content).toBeVisible();

	const before = await content.evaluate((el) => ({ scrollable: el.scrollHeight > el.clientHeight + 1 }));
	expect(before.scrollable).toBe(true);

	await content.evaluate((el) => {
		el.scrollTop = el.scrollHeight;
	});
	const m = await page.evaluate(() => {
		const doc = document.scrollingElement as HTMLElement;
		const region = document.getElementById("content") as HTMLElement;
		const header = document.querySelector("header") as HTMLElement;
		return {
			docScrollHeight: doc.scrollHeight,
			innerHeight: window.innerHeight,
			docScrollTop: doc.scrollTop,
			regionAtBottom: Math.abs(region.scrollHeight - region.clientHeight - region.scrollTop) <= 1,
			headerTop: header.getBoundingClientRect().top,
			bodyOverflowX: doc.scrollWidth,
		};
	});
	// No document scroll at any width: the shell is one viewport tall.
	expect(m.docScrollHeight).toBeLessThanOrEqual(m.innerHeight + 1);
	expect(m.docScrollTop).toBe(0);
	expect(m.regionAtBottom).toBe(true);
	expect(m.headerTop).toBeGreaterThanOrEqual(0);
	expect(m.bodyOverflowX).toBeLessThanOrEqual((page.viewportSize()?.width ?? 1440) + 1);

	// The footer is the last thing in the scroll region and is on screen after scrolling to the end.
	const footer = page.getByTestId("powered-by");
	await expect(footer).toBeInViewport();
	const box = await footer.boundingBox();
	expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual((m.innerHeight ?? 0) + 1);

	// Desktop: the sidebar is still fully on screen (it did not scroll away with the page).
	if (!isPhone(page)) {
		const sidebar = page.locator("aside");
		await expect(sidebar).toBeInViewport({ ratio: 1 });
		const sb = await sidebar.boundingBox();
		expect(sb?.y ?? 0).toBeGreaterThanOrEqual(0);
		expect((sb?.y ?? 0) + (sb?.height ?? 0)).toBeGreaterThanOrEqual(m.innerHeight - 1);
	}
});

test("signed in: the mouse wheel over the page moves the region, not the document", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await addTallContent(page, "#main");
	await page.mouse.move(isPhone(page) ? 200 : 700, 500);
	await page.mouse.wheel(0, 600);
	await expect.poll(() => page.locator("#content").evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
	expect(await page.evaluate(() => (document.scrollingElement as HTMLElement).scrollTop)).toBe(0);
});

test("signed in: a new page starts at the top of the scroll region", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await addTallContent(page, "#main");
	await page.locator("#content").evaluate((el) => {
		el.scrollTop = 800;
	});
	if (isPhone(page)) {
		await page.getByRole("button", { name: "Open menu" }).click();
		await openAdvanced(page);
		await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Usage", exact: true }).click();
	} else {
		await openAdvanced(page);
		await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Usage", exact: true }).click();
	}
	await expect(page.getByRole("heading", { level: 1, name: "Usage" })).toBeVisible();
	await expect.poll(() => page.locator("#content").evaluate((el) => el.scrollTop)).toBe(0);
});

test("signed in: the command palette and dialogs stay inside the viewport", async ({ page }, info) => {
	await logIn(page, roleOf(info));
	await addTallContent(page, "#main");
	await page.locator("#content").evaluate((el) => {
		el.scrollTop = el.scrollHeight;
	});
	await page.keyboard.press("Control+k");
	const palette = page.getByRole("dialog", { name: "Command palette" });
	await expect(palette).toBeVisible();
	const box = await palette.boundingBox();
	const vh = page.viewportSize()?.height ?? 900;
	expect(box?.y ?? -1).toBeGreaterThanOrEqual(0);
	expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(vh + 1);
	await page.keyboard.press("Escape");
});

for (const path of ["/login", "/login/key", "/signup", "/forgot"]) {
	test(`public ${path}: a normal document, no nested scroller, no gap`, async ({ page }) => {
		await page.goto(path);
		await expect(page.getByRole("main")).toBeVisible();
		await expect(page.getByTestId("powered-by")).toBeVisible();
		const m = await page.evaluate(() => {
			const doc = document.scrollingElement as HTMLElement;
			const nested = Array.from(document.querySelectorAll<HTMLElement>("body *")).filter((el) => {
				const o = getComputedStyle(el).overflowY;
				return (o === "auto" || o === "scroll") && el.scrollHeight > el.clientHeight + 1;
			});
			const root = document.getElementById("root") as HTMLElement;
			const main = document.querySelector("main") as HTMLElement;
			return {
				scrollHeight: doc.scrollHeight,
				innerHeight: window.innerHeight,
				scrollWidth: doc.scrollWidth,
				nested: nested.map((el) => el.tagName),
				mainBottom: main.getBoundingClientRect().bottom + doc.scrollTop,
				rootBottom: root.getBoundingClientRect().bottom + doc.scrollTop,
				bg: getComputedStyle(document.body).backgroundColor,
			};
		});
		expect(m.nested).toEqual([]);
		expect(m.scrollWidth).toBeLessThanOrEqual((page.viewportSize()?.width ?? 1440) + 1);
		// Whatever the page height, the content reaches the bottom of the document: no white strip.
		expect(Math.abs(m.mainBottom - m.scrollHeight)).toBeLessThanOrEqual(1);
		expect(m.bg).not.toBe("rgba(0, 0, 0, 0)");
		// And it scrolls to its end when it is taller than the window.
		await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
		await expect(page.getByTestId("powered-by")).toBeInViewport();
	});
}
