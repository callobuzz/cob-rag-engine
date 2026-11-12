/**
 * Optional video: a section video or a hero backdrop.
 *
 * The element ships without a source: the poster (or the hero's own background) is all anyone
 * sees at first. A video gets its source and plays only when the visitor has not asked for less
 * motion, data saver is off, and it is on screen. A backdrop also waits for the page to finish
 * loading and for the visitor's first move (scroll, tap, key or mouse): the browser stops
 * looking for the largest thing painted at the first input, so a full-width poster that arrives
 * later can never become it. The video pauses when it leaves the screen, and a button always
 * lets the visitor stop it.
 */
import type { Win } from "../core/types";

type Connection = { saveData?: boolean };

/** The first of these ends the browser's search for the largest paint. */
const INPUTS = ["pointerdown", "pointermove", "keydown", "scroll", "touchstart", "wheel"];

export function mayPlay(win: Win): boolean {
	const motion = win.matchMedia?.("(prefers-reduced-motion: no-preference)").matches === true;
	const saver = (win.navigator as Navigator & { connection?: Connection }).connection?.saveData === true;
	return motion && !saver;
}

function attach(video: HTMLVideoElement): void {
	if (video.dataset.ready === "1") return;
	video.dataset.ready = "1";
	const poster = video.dataset.poster;
	if (poster && !video.getAttribute("poster")) video.setAttribute("poster", poster);
	for (const source of video.querySelectorAll<HTMLSourceElement>("source[data-src]")) {
		source.src = source.dataset.src ?? "";
	}
	video.load();
}

function setLabel(toggle: HTMLElement | null, playing: boolean): void {
	if (!toggle) return;
	toggle.setAttribute("aria-pressed", String(playing));
	toggle.textContent = (playing ? toggle.dataset.pause : toggle.dataset.play) ?? toggle.textContent;
}

export function initVideo(doc: Document = document, win: Win = window as Win): void {
	const videos = Array.from(doc.querySelectorAll<HTMLVideoElement>("video[data-video]"));
	if (videos.length === 0) return;
	const allowed = mayPlay(win);
	const whenIdle = (fn: () => void): void => {
		if (typeof win.requestIdleCallback === "function") win.requestIdleCallback(fn);
		else win.setTimeout(fn, 200);
	};
	const afterLoad = (fn: () => void): void => {
		if (doc.readyState === "complete") whenIdle(fn);
		else win.addEventListener("load", () => whenIdle(fn), { once: true });
	};
	const afterInput = (fn: () => void): void => {
		let done = false;
		const go = (): void => {
			if (done) return;
			done = true;
			for (const name of INPUTS) win.removeEventListener(name, go);
			fn();
		};
		for (const name of INPUTS) win.addEventListener(name, go, { passive: true });
	};

	for (const video of videos) {
		const toggle = video.closest("figure, [data-section]")?.querySelector<HTMLElement>("[data-video-toggle]") ?? null;
		let paused = false;
		let visible = false;
		let wanted = false; // the visitor pressed Play: that wins over reduced motion and data saver
		const sync = (): void => {
			if ((!allowed && !wanted) || paused || !visible) {
				video.pause();
				setLabel(toggle, false);
				return;
			}
			attach(video);
			void video.play().catch(() => setLabel(toggle, false));
			setLabel(toggle, true);
		};
		toggle?.addEventListener("click", () => {
			if (video.dataset.ready === "1" && !video.paused) {
				paused = true;
				wanted = false;
			} else {
				paused = false;
				wanted = true;
				visible = true;
			}
			sync();
		});
		const start = (): void => {
			if (typeof win.IntersectionObserver !== "function") return;
			const observer = new win.IntersectionObserver(
				(entries) => {
					for (const entry of entries) visible = entry.isIntersecting;
					sync();
				},
				{ threshold: 0.25 },
			);
			observer.observe(video);
		};
		if (video.hasAttribute("data-backdrop")) afterLoad(() => afterInput(start));
		else start();
	}
}
