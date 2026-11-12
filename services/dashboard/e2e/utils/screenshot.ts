import type { Page } from "@playwright/test";

/**
 * A full-length screenshot of a signed-in page. The shell scrolls inside `#content`, so a plain
 * `fullPage` screenshot stops at the viewport; this lets the shell grow to its content for the
 * shot and then puts it back.
 */
export async function fullShot(page: Page, path: string): Promise<void> {
	await page.evaluate(() => {
		const saved: [HTMLElement, string | null][] = [];
		let el: HTMLElement | null = document.getElementById("content");
		while (el) {
			saved.push([el, el.getAttribute("style")]);
			el.style.height = "auto";
			el.style.maxHeight = "none";
			if (el === document.body || el === document.documentElement || el.id === "root") el.style.minHeight = "100vh";
			el.style.overflow = "visible";
			el = el.parentElement;
		}
		(window as unknown as { __fullShot: typeof saved }).__fullShot = saved;
	});
	try {
		await page.screenshot({ path, fullPage: true });
	} finally {
		await page.evaluate(() => {
			const saved = (window as unknown as { __fullShot?: [HTMLElement, string | null][] }).__fullShot ?? [];
			for (const [el, style] of saved) {
				if (style === null) el.removeAttribute("style");
				else el.setAttribute("style", style);
			}
		});
	}
}
