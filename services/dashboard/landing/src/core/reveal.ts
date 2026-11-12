import type { Win } from "./types";
/**
 * The fallback for browsers without scroll-driven animations (Firefox today): elements marked
 * `data-reveal` start slightly lowered and transparent (CSS, only under `.reveal-js`) and get
 * `.is-in` when they enter the viewport. An element that was scrolled past is revealed at once,
 * so a fast scroll never leaves a blank page, and a failsafe reveals whatever is on screen.
 */
export const FAILSAFE_MS = 5000;

export function startReveal(doc: Document, win: Win): void {
	const targets = Array.from(doc.querySelectorAll<HTMLElement>("[data-reveal]"));
	if (targets.length === 0 || typeof win.IntersectionObserver !== "function") return;

	const reveal = (element: Element): void => {
		element.classList.add("is-in");
	};
	const observer = new win.IntersectionObserver(
		(entries) => {
			for (const entry of entries) {
				if (entry.isIntersecting || entry.boundingClientRect.top < 0) {
					reveal(entry.target);
					observer.unobserve(entry.target);
				}
			}
		},
		{ rootMargin: "0px 0px -8% 0px", threshold: 0.01 },
	);
	doc.documentElement.classList.add("reveal-js");
	for (const target of targets) observer.observe(target);

	win.setTimeout(() => {
		for (const target of targets) {
			if (target.getBoundingClientRect().top < win.innerHeight * 1.5) reveal(target);
		}
	}, FAILSAFE_MS);
}
