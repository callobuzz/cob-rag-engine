import { describe, expect, it, vi } from "vitest";
import { initVideo, mayPlay } from "./video";

type W = Window & typeof globalThis;
type Show = { __show: () => void; __fire: (name: string) => void };

function fakeWin(opts: { motion: boolean; saver?: boolean }): W {
	const observers: Array<(e: Array<{ isIntersecting: boolean }>) => void> = [];
	const listeners: Record<string, Array<() => void>> = {};
	class IO {
		constructor(cb: (e: Array<{ isIntersecting: boolean }>) => void) {
			observers.push(cb);
		}
		observe(): void {}
		disconnect(): void {}
	}
	return {
		matchMedia: () => ({ matches: opts.motion }),
		navigator: { connection: { saveData: opts.saver ?? false } },
		IntersectionObserver: IO,
		requestIdleCallback: (fn: () => void) => fn(),
		setTimeout,
		addEventListener(name: string, fn: () => void): void {
			const list = listeners[name] ?? [];
			list.push(fn);
			listeners[name] = list;
		},
		removeEventListener(name: string, fn: () => void): void {
			listeners[name] = (listeners[name] ?? []).filter((f) => f !== fn);
		},
		__fire(name: string): void {
			for (const fn of [...(listeners[name] ?? [])]) fn();
		},
		__show: () => {
			for (const cb of observers) cb([{ isIntersecting: true }]);
		},
	} as unknown as W;
}

function mount(): HTMLVideoElement {
	document.body.innerHTML = `<figure><video muted playsinline loop preload="none" data-video><source data-src="/_landing/media/a.mp4"></video><button data-video-toggle data-play="Play" data-pause="Pause">Play</button></figure>`;
	const video = document.querySelector("video") as HTMLVideoElement;
	video.play = vi.fn(() => Promise.resolve());
	video.pause = vi.fn();
	video.load = vi.fn();
	return video;
}

describe("video", () => {
	it("has no source before the script decides", () => {
		const video = mount();
		expect(video.querySelector("source")?.getAttribute("src")).toBeNull();
	});

	it("plays in view when motion is allowed and data saver is off", () => {
		const video = mount();
		const win = fakeWin({ motion: true });
		initVideo(document, win);
		(win as unknown as Show).__show();
		expect(video.querySelector("source")?.getAttribute("src")).toBe("/_landing/media/a.mp4");
		expect(video.play).toHaveBeenCalled();
	});

	it("never plays under reduced motion or data saver", () => {
		for (const opts of [{ motion: false }, { motion: true, saver: true }]) {
			const video = mount();
			const win = fakeWin(opts);
			expect(mayPlay(win)).toBe(false);
			initVideo(document, win);
			(win as unknown as Show).__show();
			expect(video.play).not.toHaveBeenCalled();
			expect(video.querySelector("source")?.getAttribute("src")).toBeNull();
		}
	});

	it("a backdrop waits for the page to load and for the visitor's first move", () => {
		const video = mount();
		video.setAttribute("data-backdrop", "");
		video.dataset.poster = "/_landing/media/p.webp";
		const win = fakeWin({ motion: true });
		Object.defineProperty(document, "readyState", { value: "loading", configurable: true });
		initVideo(document, win);
		const hook = win as unknown as Show;
		hook.__show();
		expect(video.play).not.toHaveBeenCalled();
		hook.__fire("load"); // the page finished loading, but nobody has touched it
		hook.__show();
		expect(video.play).not.toHaveBeenCalled();
		expect(video.getAttribute("poster")).toBeNull();
		hook.__fire("scroll"); // the first move
		hook.__show();
		expect(video.getAttribute("poster")).toBe("/_landing/media/p.webp");
		expect(video.play).toHaveBeenCalled();
		Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
	});

	it("a click on Play works even under reduced motion or data saver, and a second click pauses", () => {
		for (const opts of [{ motion: false }, { motion: true, saver: true }]) {
			const video = mount();
			let paused = true;
			Object.defineProperty(video, "paused", { get: () => paused, configurable: true });
			video.play = vi.fn(() => {
				paused = false;
				return Promise.resolve();
			});
			video.pause = vi.fn(() => {
				paused = true;
			});
			const win = fakeWin(opts);
			initVideo(document, win);
			const toggle = document.querySelector("[data-video-toggle]") as HTMLElement;
			toggle.click();
			expect(video.querySelector("source")?.getAttribute("src")).toBe("/_landing/media/a.mp4");
			expect(video.play).toHaveBeenCalled();
			expect(toggle.getAttribute("aria-pressed")).toBe("true");
			toggle.click();
			expect(video.pause).toHaveBeenCalled();
			expect(toggle.getAttribute("aria-pressed")).toBe("false");
		}
	});

	it("a hero backdrop gets the toggle that sits outside its hidden wrapper and can be paused", () => {
		document.body.innerHTML = `<section data-section="hero"><div aria-hidden="true"><video muted playsinline loop preload="none" data-video data-backdrop><source data-src="/_landing/media/a.mp4"></video></div><button data-video-toggle data-play="Play" data-pause="Pause">Play</button></section>`;
		const video = document.querySelector("video") as HTMLVideoElement;
		let paused = true;
		Object.defineProperty(video, "paused", { get: () => paused, configurable: true });
		video.play = vi.fn(() => {
			paused = false;
			return Promise.resolve();
		});
		video.pause = vi.fn(() => {
			paused = true;
		});
		video.load = vi.fn();
		initVideo(document, fakeWin({ motion: true }));
		const toggle = document.querySelector("[data-video-toggle]") as HTMLElement;
		toggle.click();
		expect(video.play).toHaveBeenCalled();
		toggle.click();
		expect(video.pause).toHaveBeenCalled();
	});
});
