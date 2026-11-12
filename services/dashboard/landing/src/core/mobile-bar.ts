import type { Win } from "./types";
/**
 * The sticky bottom bar on a phone ("Start free" and WhatsApp): hidden over the first screen,
 * shown once the hero has scrolled out of view. CSS decides that it exists only below 768 px.
 */
export function initMobileBar(doc: Document = document, win: Win = window): void {
	const bar = doc.querySelector<HTMLElement>("[data-mobile-bar]");
	const hero = doc.querySelector<HTMLElement>('[data-section="hero"]');
	if (!bar || !hero || typeof win.IntersectionObserver !== "function") {
		bar?.classList.add("is-visible");
		return;
	}
	const observer = new win.IntersectionObserver((entries) => {
		for (const entry of entries) bar.classList.toggle("is-visible", !entry.isIntersecting);
	});
	observer.observe(hero);
}
