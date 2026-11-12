import { describe, expect, it } from "vitest";
import { websiteStrings as t } from "@/lib/strings/website";
import { skipReasonWord } from "./words";

describe("website words", () => {
	it("blames the website, not the plan, when it needs a browser", () => {
		expect(t.sourceError.needs_browser).toBe(
			"Your website builds its pages with scripts, which can't be read here yet.",
		);
		expect(t.sourceError.needs_browser).not.toMatch(/plan/i);
	});

	it("says a left-out pattern was set in Learning settings", () => {
		expect(skipReasonWord({ status: "excluded", skip_reason: "pattern", http_status: null })).toBe(
			"You left this out in Learning settings",
		);
	});
});
