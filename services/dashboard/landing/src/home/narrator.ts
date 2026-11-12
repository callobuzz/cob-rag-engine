/**
 * The narrator thread and the staggered lists in browsers without scroll-driven animations.
 *
 * Where CSS can follow the scroll (`animation-timeline: view()`) it does all the work and this
 * file does nothing. Elsewhere (Firefox today) it opts the narrator bubbles and the list items
 * into the same small reveal the rest of the page uses: they start lowered and transparent and
 * get `.is-in` when they come into view. Anything scrolled past is shown at once, so a fast
 * scroll never leaves a hole.
 */
import { motionMode } from "../core/motion";
import type { Win } from "../core/types";

const TARGETS = [
	"[data-narrator]",
	".question",
	".step",
	".sample",
	".tile",
	".chapter",
	".sentences li",
	".behaviour",
	".report > div",
	".caught",
	".inbox",
].join(",");

export function initNarrator(doc: Document = document, win: Win = window as Win): void {
	const css = (win as { CSS?: { supports?: (property: string) => boolean } }).CSS;
	if (motionMode(doc, css) !== "fallback") return;
	if (typeof win.IntersectionObserver !== "function") return;
	const targets = Array.from(doc.querySelectorAll<HTMLElement>(TARGETS));
	if (targets.length === 0) return;
	doc.documentElement.classList.add("reveal-js");
	const observer = new win.IntersectionObserver(
		(entries) => {
			for (const entry of entries) {
				if (entry.isIntersecting || entry.boundingClientRect.top < 0) {
					entry.target.classList.add("is-in");
					observer.unobserve(entry.target);
				}
			}
		},
		{ rootMargin: "0px 0px -6% 0px", threshold: 0.01 },
	);
	for (const target of targets) {
		target.setAttribute("data-reveal", "");
		observer.observe(target);
	}
}
