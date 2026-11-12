import { beforeEach, describe, expect, it } from "vitest";
import { initHeader } from "./header";
import { initMobileBar } from "./mobile-bar";
import type { Win } from "./types";

describe("initHeader", () => {
	beforeEach(() => {
		document.body.innerHTML = `
			<header data-header>
				<details data-menu><summary>Menu</summary><nav><a href="/pricing">Pricing</a></nav></details>
			</header>`;
	});

	it("closes the phone menu when a link is chosen", () => {
		const fakeWindow = Object.assign(Object.create(window), {
			matchMedia: () => ({ matches: false, addEventListener: () => undefined }),
		}) as Window;
		initHeader(document, fakeWindow);
		const menu = document.querySelector<HTMLDetailsElement>("[data-menu]") as HTMLDetailsElement;
		menu.open = true;
		const link = menu.querySelector("a") as HTMLAnchorElement;
		link.addEventListener("click", (e) => e.preventDefault());
		link.click();
		expect(menu.open).toBe(false);
	});

	it("keeps the menu open on a wide screen", () => {
		const fakeWindow = Object.assign(Object.create(window), {
			matchMedia: () => ({ matches: true, addEventListener: () => undefined }),
		}) as Window;
		initHeader(document, fakeWindow);
		expect((document.querySelector("[data-menu]") as HTMLDetailsElement).open).toBe(true);
	});
});

describe("initMobileBar", () => {
	it("shows the bar at once when it cannot watch the hero", () => {
		document.body.innerHTML = '<div data-mobile-bar></div><section data-section="hero"></section>';
		const fakeWindow = Object.assign(Object.create(window), { IntersectionObserver: undefined }) as Win;
		initMobileBar(document, fakeWindow);
		expect(document.querySelector("[data-mobile-bar]")?.classList.contains("is-visible")).toBe(true);
	});

	it("does nothing without a bar", () => {
		document.body.innerHTML = "";
		expect(() => initMobileBar(document, window)).not.toThrow();
	});
});
