import { afterEach, describe, expect, it, vi } from "vitest";
import { initMotion, motionMode } from "./motion";
import { FAILSAFE_MS } from "./reveal";
import type { Win } from "./types";

type Callback = (entries: Partial<IntersectionObserverEntry>[]) => void;

class FakeObserver {
	static last: FakeObserver | null = null;
	observed: Element[] = [];
	constructor(public callback: Callback) {
		FakeObserver.last = this;
	}
	observe(element: Element) {
		this.observed.push(element);
	}
	unobserve(element: Element) {
		this.observed = this.observed.filter((e) => e !== element);
	}
	fire(entries: Partial<IntersectionObserverEntry>[]) {
		this.callback(entries);
	}
}

function page(motionOk: boolean) {
	document.documentElement.className = motionOk ? "motion-ok" : "";
	document.body.innerHTML = `
		<section class="section" data-reveal id="a">A</section>
		<section class="section" data-reveal id="b">B</section>`;
}

function win(overrides: Record<string, unknown> = {}): Win {
	const w = window as unknown as Record<string, unknown>;
	w.IntersectionObserver = FakeObserver;
	return Object.assign(window, overrides) as Win;
}

afterEach(() => {
	vi.useRealTimers();
	document.documentElement.className = "";
	FakeObserver.last = null;
});

describe("motionMode", () => {
	it("is off unless the page opted in", () => {
		page(false);
		expect(motionMode(document, { supports: () => false })).toBe("off");
	});

	it("is native where scroll-driven animations exist", () => {
		page(true);
		expect(motionMode(document, { supports: () => true })).toBe("native");
	});

	it("falls back where they do not", () => {
		page(true);
		expect(motionMode(document, { supports: () => false })).toBe("fallback");
		expect(motionMode(document, undefined)).toBe("fallback");
	});
});

describe("initMotion", () => {
	it("adds no classes at all for a visitor who asked for less motion", () => {
		page(false);
		expect(initMotion(document, win())).toBe("off");
		expect(document.documentElement.classList.contains("reveal-js")).toBe(false);
		expect(document.querySelectorAll(".is-in")).toHaveLength(0);
		expect(FakeObserver.last).toBeNull();
	});

	it("leaves everything to CSS when the browser can animate on scroll", () => {
		page(true);
		const w = win({ CSS: { supports: () => true } });
		expect(initMotion(document, w)).toBe("native");
		expect(FakeObserver.last).toBeNull();
		expect(document.documentElement.classList.contains("reveal-js")).toBe(false);
	});

	it("reveals elements as they arrive, and at once when they were scrolled past", () => {
		page(true);
		const w = win({ CSS: { supports: () => false } });
		expect(initMotion(document, w)).toBe("fallback");
		expect(document.documentElement.classList.contains("reveal-js")).toBe(true);
		const observer = FakeObserver.last as FakeObserver;
		expect(observer.observed).toHaveLength(2);
		const [a, b] = observer.observed as HTMLElement[];
		observer.fire([{ target: a, isIntersecting: true, boundingClientRect: { top: 300 } as DOMRectReadOnly }]);
		expect(a?.classList.contains("is-in")).toBe(true);
		expect(b?.classList.contains("is-in")).toBe(false);
		// a fast scroll jumps past b: it was never intersecting but now sits above the viewport
		observer.fire([{ target: b, isIntersecting: false, boundingClientRect: { top: -900 } as DOMRectReadOnly }]);
		expect(b?.classList.contains("is-in")).toBe(true);
		expect(observer.observed).toHaveLength(0);
	});

	it("shows what is on screen even if the observer never fires", () => {
		vi.useFakeTimers();
		page(true);
		const w = win({ CSS: { supports: () => false } });
		initMotion(document, w);
		vi.advanceTimersByTime(FAILSAFE_MS + 1);
		expect(document.querySelectorAll(".is-in")).toHaveLength(2);
	});

	it("does nothing without an IntersectionObserver or without anything to reveal", () => {
		page(true);
		const w = win({ CSS: { supports: () => false } });
		(w as unknown as Record<string, unknown>).IntersectionObserver = undefined;
		initMotion(document, w);
		expect(document.documentElement.classList.contains("reveal-js")).toBe(false);
	});
});
