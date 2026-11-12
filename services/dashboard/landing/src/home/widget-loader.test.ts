import { describe, expect, it } from "vitest";
import { initWidgetLoader } from "./widget-loader";

function page(): HTMLElement {
	document.head.innerHTML = "";
	document.body.innerHTML = `<button data-ask data-ask-key="pk_demo" data-ask-text="Fees kitni hai?">Ask it yourself</button>`;
	initWidgetLoader(document);
	return document.querySelector("[data-ask]") as HTMLElement;
}

describe("ask it yourself", () => {
	it("fetches nothing before the click", () => {
		page();
		expect(document.querySelectorAll("script").length).toBe(0);
	});

	it("one click injects exactly one widget script with the key, open and the question", () => {
		const button = page();
		button.click();
		const scripts = document.querySelectorAll("script");
		expect(scripts.length).toBe(1);
		const script = scripts[0] as HTMLScriptElement;
		expect(script.getAttribute("src")).toBe("/widget.js");
		expect(script.getAttribute("data-key")).toBe("pk_demo");
		expect(script.getAttribute("data-open")).toBe("true");
		expect(script.getAttribute("data-ask")).toBe("Fees kitni hai?");
	});

	it("a second click only re-opens the loaded widget", () => {
		const button = page();
		let opened = 0;
		(window as unknown as { __cobRagWidgetOpen: () => void }).__cobRagWidgetOpen = () => {
			opened += 1;
		};
		button.click();
		button.click();
		expect(document.querySelectorAll("script").length).toBe(1);
		expect(opened).toBe(1);
	});

	it("cuts the question at 200 characters", () => {
		const button = page();
		button.setAttribute("data-ask-text", "x".repeat(300));
		button.click();
		expect(document.querySelector("script")?.getAttribute("data-ask")?.length).toBe(200);
	});
});
