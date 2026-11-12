import { describe, expect, it } from "vitest";
import { docsHref, parseDocsHref, slugFromSplat } from "./slug";

describe("docs slugs", () => {
	it("builds and parses deep links", () => {
		expect(docsHref("screens/widget")).toBe("/docs/screens/widget");
		expect(docsHref("screens/widget", "embed-the-widget")).toBe("/docs/screens/widget#embed-the-widget");
		expect(parseDocsHref("/docs/api/assistant#list")).toEqual({ slug: "api/assistant", anchor: "list" });
		expect(parseDocsHref("/docs")).toEqual({ slug: "", anchor: "" });
	});

	it("leaves other paths alone", () => {
		expect(parseDocsHref("/widget")).toBeNull();
		expect(parseDocsHref("/docsx/y")).toBeNull();
	});

	it("trims the splat", () => {
		expect(slugFromSplat("/screens/widget/")).toBe("screens/widget");
		expect(slugFromSplat(undefined)).toBe("");
		expect(slugFromSplat("")).toBe("");
	});
});
