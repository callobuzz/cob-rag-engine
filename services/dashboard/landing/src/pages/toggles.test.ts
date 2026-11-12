import { afterEach, describe, expect, it, vi } from "vitest";
import { initToggles, readChoice, STORAGE_KEY } from "./toggles";

function page(): HTMLElement {
	document.body.innerHTML = `
	<div data-pricing data-currency="INR" data-period="monthly">
	  <a data-toggle="currency" data-value="INR" aria-current="true" href="?currency=INR">INR</a>
	  <a data-toggle="currency" data-value="USD" href="?currency=USD">USD</a>
	  <a data-toggle="period" data-value="monthly" aria-current="true" href="?period=monthly">Monthly</a>
	  <a data-toggle="period" data-value="yearly" href="?period=yearly">Yearly</a>
	  <ul><li data-card>
	    <div data-variant data-currency="INR" data-period="monthly">m</div>
	    <div data-variant data-currency="INR" data-period="yearly" hidden>y</div>
	    <div data-variant data-currency="USD" data-period="monthly" hidden>$</div>
	  </li></ul>
	</div>`;
	return document.querySelector("[data-pricing]") as HTMLElement;
}

const visible = (): string[] =>
	[...document.querySelectorAll<HTMLElement>("[data-variant]")]
		.filter((v) => !v.hidden)
		.map((v) => v.textContent ?? "");

afterEach(() => {
	localStorage.clear();
	vi.restoreAllMocks();
});

describe("the toggles", () => {
	it("swap the visible variant without a reload and remember the choice", () => {
		const root = page();
		initToggles(document, window);
		(document.querySelector('[data-toggle="period"][data-value="yearly"]') as HTMLElement).click();
		expect(visible()).toEqual(["y"]);
		expect(root.dataset.period).toBe("yearly");
		expect(document.querySelector('[data-toggle="period"][data-value="yearly"]')?.getAttribute("aria-current")).toBe(
			"true",
		);
		expect(document.querySelector('[data-toggle="period"][data-value="monthly"]')?.hasAttribute("aria-current")).toBe(
			false,
		);
		expect(readChoice(localStorage)).toEqual({ currency: "INR", period: "yearly" });
		(document.querySelector('[data-toggle="currency"][data-value="USD"]') as HTMLElement).click();
		expect(visible()).toEqual(["$"]);
	});

	it("announce the change so the calculator can follow", () => {
		const root = page();
		initToggles(document, window);
		const heard = vi.fn();
		root.addEventListener("pricing:change", heard);
		(document.querySelector('[data-toggle="period"][data-value="yearly"]') as HTMLElement).click();
		expect(heard).toHaveBeenCalledTimes(1);
	});

	it("start from the last choice when the link does not say", () => {
		localStorage.setItem(STORAGE_KEY, JSON.stringify({ currency: "INR", period: "yearly" }));
		page();
		initToggles(document, window);
		expect(visible()).toEqual(["y"]);
	});

	it("ignore a remembered value the page does not offer", () => {
		localStorage.setItem(STORAGE_KEY, JSON.stringify({ currency: "EUR", period: "weekly" }));
		page();
		initToggles(document, window);
		expect(visible()).toEqual(["m"]);
	});

	it("keep working with storage blocked", () => {
		const blocked = {
			get localStorage(): Storage {
				throw new Error("blocked");
			},
			location: window.location,
		} as unknown as Window;
		page();
		initToggles(document, blocked);
		(document.querySelector('[data-toggle="period"][data-value="yearly"]') as HTMLElement).click();
		expect(visible()).toEqual(["y"]);
	});

	it("do nothing on a page without prices", () => {
		document.body.innerHTML = "<p>hello</p>";
		expect(() => initToggles(document, window)).not.toThrow();
	});
});
