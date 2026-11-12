// The home page's script entry (LP2): the hero conversation, the narrator fallback, the
// "Ask it yourself" loader and the optional video. Everything is progressive: the page is complete
// without any of it.
import "../../styles/home.css";
import { play } from "./chat-player";
import { initNarrator } from "./narrator";
import { initVideo } from "./video";
import { initWidgetLoader } from "./widget-loader";

function startHero(doc: Document): void {
	const motion = doc.documentElement.classList.contains("motion-ok");
	const hero = doc.querySelector<HTMLElement>('[data-section="hero"]');
	if (!motion || !hero || typeof IntersectionObserver !== "function") return;
	const begin = (): void => {
		const observer = new IntersectionObserver((entries) => {
			if (!entries.some((entry) => entry.isIntersecting)) return;
			observer.disconnect();
			for (const chat of hero.querySelectorAll<HTMLElement>("[data-chat]")) play(chat, { motion });
		});
		observer.observe(hero);
	};
	// After the first paint, so the headline stays the largest thing painted.
	if (typeof requestIdleCallback === "function") requestIdleCallback(begin, { timeout: 600 });
	else setTimeout(begin, 300);
}

function start(): void {
	initWidgetLoader();
	initNarrator();
	initVideo();
	startHero(document);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
else start();
