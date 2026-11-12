import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/** A selector AxeBuilder.exclude() takes (may reach into an iframe and a shadow root). */
export type AxeExclude = Parameters<AxeBuilder["exclude"]>[0];

/**
 * README "Accessibility": axe runs in every Playwright test; `serious` and `critical` violations
 * fail. `moderate` ones must be justified in the spec that introduces them (none in D1). `exclude`
 * leaves out nodes a spec cannot fix itself, each justified where it is passed (D6: widget.js).
 */

export async function expectNoAxeViolations(page: Page, context = "", exclude: AxeExclude[] = []): Promise<void> {
	// No half-finished open/close animations under the contrast check.
	await page.emulateMedia({ reducedMotion: "reduce" });
	let builder = new AxeBuilder({ page });
	for (const e of exclude) builder = builder.exclude(e);
	const results = await builder
		.withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
		// Radix focus guards and toasts' off-screen live regions are not content.
		.exclude("[data-radix-focus-guard]")
		.analyze();
	const blocking = results.violations.filter(
		(v) => v.impact === "serious" || v.impact === "critical" || v.impact === "moderate",
	);
	const summary = blocking.map(
		(v) => `${v.impact} ${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`,
	);
	expect(summary, `axe violations ${context}`).toEqual([]);
}
