import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { initLang, LANG_KEY, pickRedirect } from "./lang";

const links = [
	{ lang: "en", href: "/" },
	{ lang: "hi", href: "/hi/" },
];

describe("pickRedirect", () => {
	it("offers the remembered language only on the default home page", () => {
		expect(pickRedirect("en", "hi", links, "/")).toBe("/hi/");
		expect(pickRedirect("en", "hi", links, "/pricing")).toBeNull();
		expect(pickRedirect("hi", "hi", links, "/")).toBeNull();
		expect(pickRedirect("en", null, links, "/")).toBeNull();
		expect(pickRedirect("en", "hi-Latn", links, "/")).toBeNull();
	});
});

describe("initLang", () => {
	beforeEach(() => {
		localStorage.clear();
		document.documentElement.lang = "en";
		document.body.innerHTML = `
			<span data-lang-switch>
				<a href="/" hreflang="en">English</a>
				<a href="/hi/" hreflang="hi">हिन्दी</a>
			</span>`;
	});
	afterEach(() => localStorage.clear());

	it("remembers a click on the switch", () => {
		initLang(document, window);
		document.querySelector<HTMLAnchorElement>('a[hreflang="hi"]')?.addEventListener("click", (e) => e.preventDefault());
		document.querySelector<HTMLAnchorElement>('a[hreflang="hi"]')?.click();
		expect(localStorage.getItem(LANG_KEY)).toBe("hi");
	});

	it("survives storage being blocked", () => {
		const fake = {
			...window,
			localStorage: {
				getItem() {
					throw new Error("blocked");
				},
				setItem() {
					throw new Error("blocked");
				},
			},
			location: { pathname: "/", origin: "http://x", replace: () => undefined },
		} as unknown as Window;
		expect(() => initLang(document, fake)).not.toThrow();
	});

	it("does nothing on a page without the switch", () => {
		document.body.innerHTML = "";
		expect(() => initLang(document, window)).not.toThrow();
	});
});
