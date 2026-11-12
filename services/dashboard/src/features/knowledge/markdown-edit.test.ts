import { describe, expect, it } from "vitest";
import { applyFormat } from "./markdown-edit";

describe("article editor formatting", () => {
	it("heading prefixes the current line and toggles back", () => {
		const text = "Cost\nFrom ₹4,500";
		const on = applyFormat(text, 2, 2, "heading");
		expect(on.text).toBe("## Cost\nFrom ₹4,500");
		const off = applyFormat(on.text, 4, 4, "heading");
		expect(off.text).toBe(text);
	});

	it("heading replaces another heading level", () => {
		expect(applyFormat("# Cost", 0, 0, "heading").text).toBe("## Cost");
	});

	it("bullet prefixes every selected line and skips blank ones", () => {
		const text = "Root canal\n\nCleaning\nFilling";
		const on = applyFormat(text, 0, text.length, "bullet");
		expect(on.text).toBe("- Root canal\n\n- Cleaning\n- Filling");
		expect(applyFormat(on.text, 0, on.text.length, "bullet").text).toBe(text);
	});

	it("bold wraps the selection, and unwraps it", () => {
		const on = applyFormat("Open on Sunday", 8, 14, "bold");
		expect(on.text).toBe("Open on **Sunday**");
		expect(on.text.slice(on.start, on.end)).toBe("Sunday");
		const off = applyFormat(on.text, 8, 18, "bold");
		expect(off.text).toBe("Open on Sunday");
	});

	it("bold with nothing selected inserts a word to type over", () => {
		const on = applyFormat("Hi ", 3, 3, "bold");
		expect(on.text).toBe("Hi **bold**");
		expect(on.text.slice(on.start, on.end)).toBe("bold");
	});
});
